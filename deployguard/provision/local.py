from __future__ import annotations

import shutil
import subprocess

from rich.console import Console

from deployguard.config import AppConfig

console = Console()

_PROFILE = "deployguard"
_NAMESPACE = "deployguard"


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
    import json
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
    import json
    try:
        addons = json.loads(result.stdout or "{}")
        return addons.get("ingress", {}).get("Status") == "enabled"
    except (ValueError, TypeError):
        return False


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

    console.print(f"\n[bold green]Cluster ready.[/bold green] Run [cyan]dg deploy[/cyan] to ship.\n")
