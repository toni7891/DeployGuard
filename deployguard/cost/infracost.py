"""Wrap the infracost CLI for cost estimation."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading

# Hard ceiling for a real scan. A login/org prompt is detected from the output
# and stopped long before this, so `dg cost` never sits silently on OAuth.
_TIMEOUT_SECONDS = 90

# Output fragments (lower-cased) meaning infracost wants an interactive login or
# an organization before it will price anything.
_AUTH_NEEDLES = (
    "needs login",
    "need to log in",
    "please log in",
    "log in to infracost",
    "infracost auth login",
    "not authenticated",
    "unauthenticated",
    "no associated organization",
    "no organization",
    "enter the code",
    "device code",
)

AUTH_MESSAGE = (
    "Cost estimate unavailable: Infracost needs a login or organization "
    "(run: infracost auth login). Showing resources only."
)


class InfracostUnavailable(RuntimeError):
    """Infracost could not produce an estimate; the message is user-facing and short."""


def _needs_auth(text: str) -> bool:
    lowered = text.lower()
    return any(needle in lowered for needle in _AUTH_NEEDLES)


# ── Private helper (monkeypatch-friendly) ──────────────────────────────────────

def _run(cmd: list[str], timeout: float) -> tuple[int | None, str, str, bool]:
    """Run cmd non-interactively. Returns (returncode, stdout, stderr, auth_prompt_seen).

    stdin is closed so nothing can wait on the keyboard. Both streams are read
    as they arrive; the process is killed as soon as a login/org prompt shows
    up, or when the timeout expires (returncode None).
    """
    env = {**os.environ, "INFRACOST_SKIP_UPDATE_CHECK": "true", "INFRACOST_NO_COLOR": "true"}
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,  # so a kill also reaches any helper it spawned
    )
    out: list[str] = []
    err: list[str] = []
    auth_seen = threading.Event()

    def _pump(stream, sink: list[str]) -> None:
        for line in stream:
            sink.append(line)
            if _needs_auth(line):
                auth_seen.set()

    readers = [
        threading.Thread(target=_pump, args=(proc.stdout, out), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, err), daemon=True),
    ]
    for t in readers:
        t.start()

    returncode: int | None = None
    waited = 0.0
    while waited < timeout and not auth_seen.is_set():
        try:
            returncode = proc.wait(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            waited += 0.2

    if returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, AttributeError):
            proc.kill()
        proc.wait()
    for t in readers:
        t.join(timeout=2)
    return returncode, "".join(out), "".join(err), auth_seen.is_set()


# ── Public API ─────────────────────────────────────────────────────────────────

def run_infracost(tf_dir: str, timeout: float = _TIMEOUT_SECONDS) -> dict:
    """Run infracost scan on tf_dir and return parsed JSON.

    Returns empty dict when no Terraform files are found.
    Raises RuntimeError when the infracost binary is missing, and
    InfracostUnavailable (a RuntimeError with a one-line, user-facing message)
    when it needs a login/organization, times out, or fails. Never prompts.
    """
    if not shutil.which("infracost"):
        raise RuntimeError(
            "infracost not found. Install it and re-run `dg doctor` to verify."
        )

    # Device flow: if auth is missing, infracost prints a code instead of
    # opening a browser and waiting on a localhost callback.
    returncode, stdout, stderr, auth_seen = _run(
        ["infracost", "scan", tf_dir, "--json", "--no-color", "--oauth-use-device-flow"],
        timeout,
    )

    if auth_seen or _needs_auth(stdout + stderr):
        raise InfracostUnavailable(AUTH_MESSAGE)

    if returncode is None:
        raise InfracostUnavailable(
            f"Cost estimate unavailable: infracost did not finish within {int(timeout)}s. "
            "Showing resources only."
        )

    if returncode != 0:
        if "no terraform" in stderr.lower():
            return {}
        last = (stderr.strip().splitlines() or ["unknown error"])[-1][:160]
        raise InfracostUnavailable(
            f"Cost estimate unavailable: infracost failed ({last}). Showing resources only."
        )

    stdout = stdout.strip()
    if not stdout:
        return {}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise InfracostUnavailable(
            "Cost estimate unavailable: infracost returned unreadable output. "
            "Showing resources only."
        ) from None
