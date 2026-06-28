"""Pre-deployment green-deployment validation."""
from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import requests
import yaml
from pydantic import BaseModel
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from deployguard.config import AppConfig
from deployguard.manifest import ServiceManifest

console = Console()


class PreCheckResult(BaseModel):
    passed: bool
    reason: str | None = None


# ── Private helpers (monkeypatch-friendly) ─────────────────────────────────────

def _patch_green_manifests(manifest: ServiceManifest, k8s_dir: Path) -> str:
    """Read k8s/ manifests, rename Deployment to {name}-green, inject image."""
    docs: list[dict] = []
    for path in sorted(k8s_dir.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if doc is None:
                continue
            if doc.get("kind") == "Deployment":
                doc["metadata"]["name"] = f"{manifest.name}-green"
                containers = (
                    doc.get("spec", {})
                    .get("template", {})
                    .get("spec", {})
                    .get("containers", [])
                )
                for c in containers:
                    c["image"] = manifest.image or manifest.name
                    c["imagePullPolicy"] = "Never"
            docs.append(doc)
    return yaml.dump_all(docs, default_flow_style=False)


def _run_kubectl(
    cmd: list[str],
    timeout: int = 300,
    stdin: str | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin)


def _open_port_forward(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _http_get(url: str, timeout: float = 5.0) -> requests.Response:
    return requests.get(url, timeout=timeout)


def _delete_green(name: str, namespace: str) -> None:
    subprocess.run(
        ["kubectl", "delete", "deployment", f"{name}-green",
         "-n", namespace, "--ignore-not-found"],
        capture_output=True,
        timeout=30,
    )


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _get_pod_counts(name: str, namespace: str) -> tuple[int, int]:
    """Return (ready_pods, total_pods) by inspecting pods directly.

    Counts at the pod level instead of deployment.status.readyReplicas, which
    can lag or stay null when the endpoint controller has RBAC issues.

    Filters by ownerReference ReplicaSet name matching `{name}-{alphanum_hash}`.
    This correctly separates stable (`movie-api-abc123`) from green
    (`movie-api-green-abc123`) even though both share `app=movie-api` labels.
    """
    import re
    rs_pattern = re.compile(rf"^{re.escape(name)}-[a-z0-9]+$")

    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return 0, 0
    try:
        items = json.loads(result.stdout).get("items", [])
        owned = [
            p for p in items
            if any(
                rs_pattern.match(ref.get("name", ""))
                for ref in p.get("metadata", {}).get("ownerReferences", [])
                if ref.get("kind") == "ReplicaSet"
            )
        ]
        total = len(owned)
        ready = sum(
            1 for p in owned
            if any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in p.get("status", {}).get("conditions", [])
            )
        )
        return ready, total
    except (json.JSONDecodeError, KeyError, TypeError):
        return 0, 0


def _render_precheck_panel(
    manifest: ServiceManifest,
    stable_ready: int,
    stable_total: int,
    green_ready: int,
    green_total: int,
    phase: str,
    health_status: str = "",
) -> Panel:
    table = Table(box=None, padding=(0, 2), show_header=False)
    table.add_column("Role", width=8, style="dim")
    table.add_column("Name", width=26)
    table.add_column("Pods", width=6)
    table.add_column("Status")

    # Stable row
    if stable_total > 0 and stable_ready == stable_total:
        s_icon, s_status = "[green]●[/green]", "[green]Running[/green]"
    else:
        s_icon, s_status = "[dim]●[/dim]", f"[dim]{stable_ready}/{stable_total}[/dim]"
    table.add_row("stable", manifest.name, f"{stable_ready}/{stable_total}", f"{s_icon} {s_status}")

    # Green row
    if green_total == 0:
        g_icon, g_status = "[dim]○[/dim]", "[dim]Pending[/dim]"
    elif green_ready == green_total:
        g_icon, g_status = "[green]●[/green]", "[green]Ready[/green]"
    else:
        g_icon = "[cyan]⟳[/cyan]"
        g_status = f"[cyan]Starting {green_ready}/{green_total}[/cyan]"
    table.add_row("green", f"{manifest.name}-green", f"{green_ready}/{green_total}", f"{g_icon} {g_status}")

    if health_status:
        table.add_row("", "", "", "")
        table.add_row("", f"[dim]{manifest.health_readiness}[/dim]", "", health_status)

    phase_color = {
        "starting": "cyan",
        "checking": "cyan",
        "passed": "green",
        "failed": "red",
        "cleanup": "yellow",
    }.get(phase, "white")

    return Panel(
        table,
        title=f"[bold]Pre-check · {manifest.name}[/bold]",
        subtitle=f"[{phase_color}]{phase}[/{phase_color}]",
        border_style=phase_color if phase in ("passed", "failed") else "dim",
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def run_precheck(
    manifest: ServiceManifest,
    config: AppConfig,
    k8s_dir: Path | None = None,
) -> PreCheckResult:
    """Apply a green deployment, wait for readiness, smoke-test /readyz.

    On success returns PreCheckResult(passed=True).
    On any failure: deletes the green deployment and returns passed=False with reason.
    k8s_dir defaults to Path("k8s") — override in tests.
    """
    k8s_dir = k8s_dir or Path("k8s")
    name_green = f"{manifest.name}-green"
    ns = manifest.namespace

    # 1. Apply patched manifests as the green deployment
    patched = _patch_green_manifests(manifest, k8s_dir)
    apply_result = _run_kubectl(
        ["kubectl", "apply", "-f", "-", "-n", ns],
        stdin=patched,
    )
    if apply_result.returncode != 0:
        _delete_green(manifest.name, ns)
        detail = (apply_result.stderr or apply_result.stdout or "").strip()
        return PreCheckResult(passed=False, reason=f"kubectl apply failed: {detail}")

    # 2. Wait for green deployment to reach ready state — poll pod counts in Live panel
    rollout_result_holder: list[subprocess.CompletedProcess | None] = [None]
    rollout_exception: list[Exception | None] = [None]

    def _wait_rollout() -> None:
        try:
            rollout_result_holder[0] = _run_kubectl(
                [
                    "kubectl", "rollout", "status",
                    f"deployment/{name_green}",
                    "-n", ns,
                    f"--timeout={config.deploy.smoke_timeout}s",
                ],
                timeout=config.deploy.smoke_timeout + 10,
            )
        except subprocess.TimeoutExpired as exc:
            rollout_exception[0] = exc

    t = threading.Thread(target=_wait_rollout, daemon=True)
    t.start()

    stable_ready, stable_total = _get_pod_counts(manifest.name, ns)

    with Live(
        _render_precheck_panel(manifest, stable_ready, stable_total, 0, 0, "starting"),
        console=console,
        refresh_per_second=4,
        transient=False,
    ) as live:
        while t.is_alive():
            sr, st = _get_pod_counts(manifest.name, ns)
            gr, gt = _get_pod_counts(name_green, ns)
            live.update(_render_precheck_panel(manifest, sr, st, gr, gt, "starting"))
            _sleep(1.0)
        t.join()

        # Timed out or rollout failed
        if rollout_exception[0] is not None or (
            rollout_result_holder[0] is not None
            and rollout_result_holder[0].returncode != 0
        ):
            sr, st = _get_pod_counts(manifest.name, ns)
            gr, gt = _get_pod_counts(name_green, ns)
            live.update(_render_precheck_panel(
                manifest, sr, st, gr, gt, "failed",
                "[red]Readiness timeout[/red]",
            ))
            _sleep(0.5)
            _delete_green(manifest.name, ns)
            return PreCheckResult(passed=False, reason="Readiness timeout")

        sr, st = _get_pod_counts(manifest.name, ns)
        gr, gt = _get_pod_counts(name_green, ns)
        live.update(_render_precheck_panel(
            manifest, sr, st, gr, gt, "checking",
            "[cyan]checking...[/cyan]",
        ))

        # 3. Smoke test /readyz via temporary port-forward
        local_port = _free_port()
        pf = _open_port_forward(
            [
                "kubectl", "port-forward", f"deployment/{name_green}",
                f"{local_port}:{manifest.port}", "-n", ns,
            ]
        )
        try:
            _sleep(1.0)
            url = f"http://localhost:{local_port}{manifest.health_readiness}"
            try:
                resp = _http_get(url)
            except requests.RequestException as exc:
                live.update(_render_precheck_panel(
                    manifest, sr, st, gr, gt, "failed",
                    f"[red]unreachable: {exc}[/red]",
                ))
                _sleep(0.5)
                _delete_green(manifest.name, ns)
                return PreCheckResult(passed=False, reason=f"/readyz unreachable: {exc}")

            if resp.status_code != 200:
                live.update(_render_precheck_panel(
                    manifest, sr, st, gr, gt, "failed",
                    f"[red]HTTP {resp.status_code}[/red]",
                ))
                _sleep(0.5)
                _delete_green(manifest.name, ns)
                return PreCheckResult(passed=False, reason=f"/readyz returned {resp.status_code}")

            live.update(_render_precheck_panel(
                manifest, sr, st, gr, gt, "passed",
                "[green]HTTP 200 ✓[/green]",
            ))
            _sleep(0.5)
            _delete_green(manifest.name, ns)
            return PreCheckResult(passed=True, reason=None)
        finally:
            pf.terminate()
            try:
                pf.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pf.kill()
