"""Gradual traffic shift via nginx-ingress canary weights with Rich Live display."""
from __future__ import annotations

import subprocess
import time
from typing import Callable

import yaml
from pydantic import BaseModel
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from deployguard.config import AppConfig
from deployguard.manifest import ServiceManifest

console = Console()


class RolloutStep(BaseModel):
    weight: int
    status: str = "pending"  # pending | active | done | failed


# ── YAML builders ──────────────────────────────────────────────────────────────

def _canary_deployment_yaml(manifest: ServiceManifest) -> str:
    doc = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": f"{manifest.name}-canary",
            "namespace": manifest.namespace,
        },
        "spec": {
            "replicas": manifest.replicas,
            "selector": {"matchLabels": {"app": manifest.name, "track": "canary"}},
            "template": {
                "metadata": {"labels": {"app": manifest.name, "track": "canary"}},
                "spec": {
                    "containers": [
                        {
                            "name": manifest.name,
                            "image": manifest.image or manifest.name,
                            "imagePullPolicy": "Never",
                            "ports": [{"containerPort": manifest.port}],
                        }
                    ]
                },
            },
        },
    }
    return yaml.dump(doc, default_flow_style=False)


def _canary_service_yaml(manifest: ServiceManifest) -> str:
    doc = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": f"{manifest.name}-canary",
            "namespace": manifest.namespace,
        },
        "spec": {
            "selector": {"app": manifest.name, "track": "canary"},
            "ports": [{"port": 80, "targetPort": manifest.port}],
        },
    }
    return yaml.dump(doc, default_flow_style=False)


def _canary_ingress_yaml(manifest: ServiceManifest, weight: int) -> str:
    doc = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": f"{manifest.name}-canary",
            "namespace": manifest.namespace,
            "annotations": {
                "nginx.ingress.kubernetes.io/canary": "true",
                "nginx.ingress.kubernetes.io/canary-weight": str(weight),
            },
        },
        "spec": {
            "rules": [
                {
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": f"{manifest.name}-canary",
                                        "port": {"number": 80},
                                    }
                                },
                            }
                        ]
                    }
                }
            ]
        },
    }
    return yaml.dump(doc, default_flow_style=False)


# ── Private helpers (monkeypatch-friendly) ─────────────────────────────────────

def _run_kubectl(
    cmd: list[str],
    timeout: int = 120,
    stdin: str | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _apply_canary(manifest: ServiceManifest, weight: int) -> None:
    combined = (
        _canary_deployment_yaml(manifest)
        + "---\n"
        + _canary_service_yaml(manifest)
        + "---\n"
        + _canary_ingress_yaml(manifest, weight)
    )
    result = _run_kubectl(
        ["kubectl", "apply", "-f", "-", "-n", manifest.namespace],
        stdin=combined,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to apply canary resources:\n"
            f"{(result.stderr or result.stdout or '').strip()}"
        )


def _patch_ingress_weight(manifest: ServiceManifest, weight: int) -> None:
    result = _run_kubectl(
        [
            "kubectl", "annotate", "ingress", f"{manifest.name}-canary",
            f"nginx.ingress.kubernetes.io/canary-weight={weight}",
            "--overwrite", "-n", manifest.namespace,
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to patch canary weight to {weight}:\n"
            f"{(result.stderr or result.stdout or '').strip()}"
        )


def _promote(manifest: ServiceManifest) -> None:
    """Patch the stable deployment to the canary image and wait for rollout."""
    image = manifest.image or manifest.name
    result = _run_kubectl(
        [
            "kubectl", "set", "image",
            f"deployment/{manifest.name}",
            f"{manifest.name}={image}",
            "-n", manifest.namespace,
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to promote stable deployment:\n"
            f"{(result.stderr or result.stdout or '').strip()}"
        )
    _run_kubectl(
        [
            "kubectl", "rollout", "status",
            f"deployment/{manifest.name}",
            "-n", manifest.namespace,
            "--timeout=120s",
        ],
        timeout=130,
    )


def _delete_canary(manifest: ServiceManifest) -> None:
    ns = manifest.namespace
    name = manifest.name
    for kind, resource in [
        ("deployment", f"{name}-canary"),
        ("service", f"{name}-canary"),
        ("ingress", f"{name}-canary"),
    ]:
        _run_kubectl(
            ["kubectl", "delete", kind, resource, "-n", ns, "--ignore-not-found"],
        )


# ── Rich display ───────────────────────────────────────────────────────────────

_STATUS_STYLE = {
    "pending": ("[dim]○ pending[/dim]", ""),
    "active": ("[bold cyan]● active[/bold cyan]", "cyan"),
    "done": ("[green]✓ done[/green]", "green"),
    "failed": ("[bold red]✗ failed[/bold red]", "red"),
}


def _render_steps(name: str, steps: list[RolloutStep]) -> Table:
    table = Table(box=None, padding=(0, 2), show_header=False)
    table.add_column("Step", style="dim", width=8)
    table.add_column("Weight", width=6)
    table.add_column("Bar", width=22)
    table.add_column("Status")

    total = len(steps)
    for i, step in enumerate(steps):
        label = f"{i + 1}/{total}"
        weight_str = f"{step.weight:>3}%"
        filled = int(step.weight / 100 * 20)
        bar_filled = "█" * filled
        bar_empty = "░" * (20 - filled)
        status_markup, bar_style = _STATUS_STYLE.get(step.status, ("", ""))
        bar = f"[{bar_style}]{bar_filled}[/{bar_style}]{bar_empty}" if bar_style else bar_empty + bar_filled
        table.add_row(label, weight_str, bar, status_markup)

    return table


# ── Public API ─────────────────────────────────────────────────────────────────

def make_prometheus_checker(
    manifest: ServiceManifest,
    config: AppConfig,
    prometheus_url: str = "http://localhost:9090",
) -> Callable[[int, int], bool]:
    """Return an on_step callback that gates each rollout step on Prometheus error rate.

    Returns False (triggering rollback) when the error rate exceeds
    config.deploy.error_rate_threshold.  Prometheus being unreachable returns
    0.0 from get_error_rate(), so the gate passes — metrics down never blocks a deploy.
    """
    from deployguard.engine.metrics import get_error_rate

    def _check(step_index: int, weight: int) -> bool:
        rate = get_error_rate(manifest.name, manifest.namespace, prometheus_url)
        if rate > config.deploy.error_rate_threshold:
            console.print(
                f"[bold red]✗[/bold red] Error rate [red]{rate:.2f}%[/red] exceeds "
                f"threshold {config.deploy.error_rate_threshold}% at {weight}% traffic — "
                "triggering rollback."
            )
            return False
        return True

    return _check


def rollback(manifest: ServiceManifest) -> None:
    """Delete canary resources and undo the stable deployment."""
    _delete_canary(manifest)
    _run_kubectl(
        [
            "kubectl", "rollout", "undo",
            f"deployment/{manifest.name}",
            "-n", manifest.namespace,
        ]
    )


def rollout_traffic(
    manifest: ServiceManifest,
    config: AppConfig,
    on_step: Callable[[int, int], bool],
) -> bool:
    """Shift traffic through rollout_steps via nginx canary weights.

    on_step(step_index, weight) is called after each dwell period.
    Return False from on_step to trigger rollback and return False.
    Returns True when all steps complete and the canary is promoted to stable.
    """
    steps = [RolloutStep(weight=w) for w in config.deploy.rollout_steps]

    console.print(f"\n[bold]Rolling out [cyan]{manifest.name}[/cyan][/bold]\n")

    _apply_canary(manifest, weight=0)

    with Live(
        _render_steps(manifest.name, steps),
        console=console,
        refresh_per_second=4,
        transient=False,
    ) as live:
        for i, step in enumerate(steps):
            step.status = "active"
            live.update(_render_steps(manifest.name, steps))

            _patch_ingress_weight(manifest, step.weight)
            _sleep(10)

            if not on_step(i, step.weight):
                step.status = "failed"
                live.update(_render_steps(manifest.name, steps))
                rollback(manifest)
                return False

            step.status = "done"
            live.update(_render_steps(manifest.name, steps))

    _promote(manifest)
    _delete_canary(manifest)
    console.print(f"\n[bold green]✓ Rollout complete.[/bold green] "
                  f"Canary promoted to stable.\n")
    return True
