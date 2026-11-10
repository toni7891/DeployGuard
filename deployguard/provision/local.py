from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from rich.console import Console

from deployguard.config import AppConfig

console = Console()

_PROFILE = "deployguard"
_NAMESPACE = "deployguard"

# Request metrics for the `dg deploy` error-rate gate. The minikube ingress
# addon does not export nginx_ingress_controller_requests unless the
# controller runs with --enable-metrics=true.
_INGRESS_NS = "ingress-nginx"
_INGRESS_DEPLOYMENT = "ingress-nginx-controller"
_METRICS_FLAG = "--enable-metrics=true"

# Small Prometheus that scrapes the ingress controller (package data).
PROMETHEUS_MANIFEST = Path(__file__).parent / "prometheus.yaml"
_MONITORING_NS = "monitoring"


def _run(
    cmd: list[str],
    label: str,
    stdin: str | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    with console.status(f"[dim]{label}...[/dim]"):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                input=stdin,
                timeout=timeout,
            )
        except FileNotFoundError:
            console.print(f"[red]✗[/red] {label}")
            raise RuntimeError(
                f"'{cmd[0]}' not found. Install it and run `dg doctor` to verify."
            )
    if result.returncode != 0:
        console.print(f"[red]✗[/red] {label}")
        detail = (result.stderr or result.stdout or "(no output)").strip()
        raise RuntimeError(f"{label} failed (exit {result.returncode}):\n{detail}")
    console.print(f"[green]✓[/green] {label}")
    return result


def _cluster_running() -> bool:
    if not shutil.which("minikube"):
        raise RuntimeError("minikube not found. Install it: brew install minikube")
    result = subprocess.run(
        ["minikube", "status", "--profile", _PROFILE, "--output", "json"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return False
    try:
        status = json.loads(result.stdout or "{}")
        return status.get("Host", "") == "Running"
    except ValueError:
        return False


def _ingress_enabled() -> bool:
    result = subprocess.run(
        ["minikube", "addons", "list", "--profile", _PROFILE, "--output", "json"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return False
    try:
        addons = json.loads(result.stdout or "{}")
        return addons.get("ingress", {}).get("Status") == "enabled"
    except (ValueError, TypeError):
        return False


def _ingress_controller_args() -> list[str] | None:
    """Args of the ingress-nginx controller container, or None if unreadable."""
    result = subprocess.run(
        ["kubectl", "get", "deployment", _INGRESS_DEPLOYMENT, "-n", _INGRESS_NS, "-o", "json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    try:
        containers = json.loads(result.stdout)["spec"]["template"]["spec"]["containers"]
        return list(containers[0].get("args") or [])
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def _enable_ingress_metrics() -> None:
    """Add --enable-metrics=true to the controller once, then wait for its rollout."""
    label = "Enabling ingress metrics"
    args = _ingress_controller_args()
    if args is None:
        console.print(f"[red]✗[/red] {label}")
        raise RuntimeError(
            f"Could not read deployment/{_INGRESS_DEPLOYMENT} in namespace {_INGRESS_NS}. "
            "Is the minikube ingress addon enabled?"
        )
    if _METRICS_FLAG in args:
        console.print(f"[green]✓[/green] {label} (already enabled)")
        return
    patch = json.dumps([{
        "op": "add",
        "path": "/spec/template/spec/containers/0/args/-",
        "value": _METRICS_FLAG,
    }])
    _run(
        ["kubectl", "patch", "deployment", _INGRESS_DEPLOYMENT, "-n", _INGRESS_NS,
         "--type=json", "-p", patch],
        label=label,
    )
    _run(
        ["kubectl", "rollout", "status", f"deployment/{_INGRESS_DEPLOYMENT}",
         "-n", _INGRESS_NS, "--timeout=180s"],
        label="Waiting for ingress controller restart",
        timeout=200,
    )


def _install_prometheus() -> None:
    """kubectl apply the bundled Prometheus (idempotent) and wait until it is ready."""
    _run(
        ["kubectl", "apply", "-f", "-"],
        label=f"Installing Prometheus ({_MONITORING_NS})",
        stdin=PROMETHEUS_MANIFEST.read_text(),
    )
    _run(
        ["kubectl", "rollout", "status", "deployment/prometheus",
         "-n", _MONITORING_NS, "--timeout=180s"],
        label="Waiting for Prometheus to be ready",
        timeout=200,
    )


def provision_local(config: AppConfig) -> None:
    console.print(f"\n[bold]Provisioning local cluster ({_PROFILE})[/bold]\n")

    # ── 1. minikube cluster ──────────────────────────────────────────────────
    if _cluster_running():
        console.print(f"[green]✓[/green] minikube cluster '{_PROFILE}' already running")
    else:
        _run(
            [
                "minikube", "start",
                "--profile", _PROFILE,
                "--driver", "docker",
            ],
            label=f"Starting minikube cluster '{_PROFILE}' (this takes ~60s)",
            timeout=300,
        )

    # ── 2. kubectl context ───────────────────────────────────────────────────
    _run(
        ["kubectl", "config", "use-context", _PROFILE],
        label=f"Setting kubectl context to {_PROFILE}",
    )

    # ── 3. ingress addon ─────────────────────────────────────────────────────
    if _ingress_enabled():
        console.print("[green]✓[/green] ingress addon already enabled — skipping")
    else:
        _run(
            ["minikube", "addons", "enable", "ingress", "--profile", _PROFILE],
            label="Enabling ingress addon",
            timeout=180,
        )

    # ── 4. deployguard namespace ─────────────────────────────────────────────
    ns_yaml = subprocess.run(
        ["kubectl", "create", "namespace", _NAMESPACE, "--dry-run=client", "-o", "yaml"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if ns_yaml.returncode != 0:
        raise RuntimeError(
            f"kubectl dry-run failed:\n{(ns_yaml.stderr or ns_yaml.stdout).strip()}"
        )
    _run(
        ["kubectl", "apply", "-f", "-"],
        label=f"Applying namespace '{_NAMESPACE}'",
        stdin=ns_yaml.stdout,
    )

    # ── 5. ingress request metrics + Prometheus (error-rate gate) ────────────
    _enable_ingress_metrics()
    _install_prometheus()

    console.print(f"\n[bold green]Cluster ready.[/bold green] Run [cyan]dg deploy[/cyan] to ship.\n")
