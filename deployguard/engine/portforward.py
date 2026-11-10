"""Managed `kubectl port-forward` for the duration of a block, plus the
Prometheus endpoint the rollout gate uses."""
from __future__ import annotations

import socket
import subprocess
import time
from contextlib import contextmanager
from typing import Iterator

from deployguard.config import AppConfig

# Namespace / Service installed by `dg provision` on the local target
# (deployguard/provision/prometheus.yaml).
PROMETHEUS_NAMESPACE = "monitoring"
PROMETHEUS_SERVICE = "svc/prometheus"
PROMETHEUS_PORT = 9090


# ── Private helpers (monkeypatch-friendly) ─────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _open_port_forward(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _port_open(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


# ── Public API ─────────────────────────────────────────────────────────────────

@contextmanager
def port_forward(
    namespace: str,
    resource: str,
    remote_port: int,
    timeout: float = 15.0,
) -> Iterator[int | None]:
    """Run `kubectl port-forward -n <namespace> <resource> <free>:<remote_port>`.

    Yields the local port once it accepts connections, or None if kubectl is
    missing, exits (e.g. the Service does not exist) or does not answer within
    `timeout`. Never raises for those cases. The kubectl process is always
    terminated on exit, including when the block raises.
    """
    port = _free_port()
    try:
        proc = _open_port_forward(
            ["kubectl", "port-forward", "-n", namespace, resource, f"{port}:{remote_port}"]
        )
    except OSError:
        yield None
        return

    try:
        ready = False
        waited = 0.0
        while waited < timeout:
            if proc.poll() is not None:
                break
            if _port_open(port):
                ready = True
                break
            _sleep(0.25)
            waited += 0.25
        yield port if ready else None
    finally:
        _stop(proc)


@contextmanager
def prometheus_endpoint(config: AppConfig) -> Iterator[str]:
    """Yield the Prometheus base URL for the error-rate gate.

    - deploy.prometheus_url set by the user: use it, start nothing.
    - local target: port-forward to the Prometheus `dg provision` installed,
      for as long as the block runs.
    - otherwise, or if the port-forward cannot start: the default URL, so the
      gate reports itself inactive exactly as before (never blocks a deploy).
    """
    deploy = config.deploy
    if deploy.prometheus_url_explicit or deploy.target != "local":
        yield deploy.effective_prometheus_url
        return
    with port_forward(PROMETHEUS_NAMESPACE, PROMETHEUS_SERVICE, PROMETHEUS_PORT) as port:
        yield f"http://localhost:{port}" if port else deploy.effective_prometheus_url
