"""Gradual traffic shift via nginx-ingress canary weights with Rich Live display."""
from __future__ import annotations

import copy
import subprocess
import time
from pathlib import Path
from typing import Callable

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
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

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_CANARY_LABELS = {"track": "canary"}


def _default_stable_deployment(manifest: ServiceManifest) -> dict:
    """The golden-path stable Deployment, rendered from the same template `dg init` uses."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    rendered = env.get_template("k8s/deployment.yaml.j2").render(
        name=manifest.name,
        namespace=manifest.namespace,
        replicas=manifest.replicas,
        port=manifest.port,
        health_liveness=manifest.health_liveness,
        health_readiness=manifest.health_readiness,
    )
    return yaml.safe_load(rendered)


def _canary_deployment_yaml(
    manifest: ServiceManifest,
    target: str = "local",
    stable: dict | None = None,
) -> str:
    """Canary Deployment cloned from the stable spec, so it is never weaker.

    `stable` is the guard-checked k8s/deployment.yaml Deployment; without it the
    golden-path template is used. Only identity fields change: name, the
    `track: canary` label (which the stable Service does not select), image,
    and imagePullPolicy (Never for local `minikube image load`, IfNotPresent
    for aws so the node pulls from the registry).
    """
    doc = copy.deepcopy(stable) if stable else _default_stable_deployment(manifest)
    labels = {"app": manifest.name, **_CANARY_LABELS}

    doc.pop("status", None)
    metadata = doc.setdefault("metadata", {})
    for key in ("resourceVersion", "uid", "creationTimestamp", "generation", "managedFields"):
        metadata.pop(key, None)
    metadata["name"] = f"{manifest.name}-canary"
    metadata["namespace"] = manifest.namespace
    metadata["labels"] = {**(metadata.get("labels") or {}), **labels}

    spec = doc.setdefault("spec", {})
    spec["replicas"] = manifest.replicas
    spec["selector"] = {"matchLabels": dict(labels)}
    template = spec.setdefault("template", {})
    pod_meta = template.setdefault("metadata", {})
    pod_meta["labels"] = {**(pod_meta.get("labels") or {}), **labels}

    containers = template.setdefault("spec", {}).setdefault("containers", [])
    for c in containers:
        if c.get("name") == manifest.name or len(containers) == 1:
            c["image"] = manifest.image or manifest.name
            c["imagePullPolicy"] = "Never" if target == "local" else "IfNotPresent"
    return yaml.dump(doc, default_flow_style=False, sort_keys=False)


def _canary_service_yaml(manifest: ServiceManifest) -> str:
    doc = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": f"{manifest.name}-canary",
            "namespace": manifest.namespace,
            "labels": {"app": manifest.name, **_CANARY_LABELS},
        },
        "spec": {
            "type": "ClusterIP",
            "selector": {"app": manifest.name, **_CANARY_LABELS},
            # Same port as the stable Service, so both Ingress backends match.
            "ports": [{"port": manifest.port, "targetPort": manifest.port, "protocol": "TCP"}],
        },
    }
    return yaml.dump(doc, default_flow_style=False, sort_keys=False)


def _canary_ingress_yaml(manifest: ServiceManifest, weight: int) -> str:
    """Canary Ingress. Host, path and class must match the stable Ingress
    (k8s/ingress.yaml) or ingress-nginx ignores the canary annotations."""
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
            "ingressClassName": "nginx",
            "rules": [
                {
                    "host": manifest.host,
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": f"{manifest.name}-canary",
                                        "port": {"number": manifest.port},
                                    }
                                },
                            }
                        ]
                    },
                }
            ],
        },
    }
    return yaml.dump(doc, default_flow_style=False, sort_keys=False)


# ── Private helpers (monkeypatch-friendly) ─────────────────────────────────────

def _run_kubectl(
    cmd: list[str],
    timeout: int = 120,
    stdin: str | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _apply_canary(
    manifest: ServiceManifest,
    weight: int,
    target: str = "local",
    stable: dict | None = None,
) -> None:
    combined = (
        _canary_deployment_yaml(manifest, target, stable)
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
    # Shift no traffic until the canary pods are Ready, or the first step
    # would gate on 5xx from a backend with no endpoints.
    ready = _run_kubectl(
        [
            "kubectl", "rollout", "status",
            f"deployment/{manifest.name}-canary",
            "-n", manifest.namespace,
            "--timeout=120s",
        ],
        timeout=130,
    )
    if ready.returncode != 0:
        raise RuntimeError(
            f"Canary deployment did not become ready:\n"
            f"{(ready.stderr or ready.stdout or '').strip()}"
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
    status = _run_kubectl(
        [
            "kubectl", "rollout", "status",
            f"deployment/{manifest.name}",
            "-n", manifest.namespace,
            "--timeout=120s",
        ],
        timeout=130,
    )
    if status.returncode != 0:
        # The stable Deployment was changed by `set image` above, so undo it.
        rollback(manifest, undo_stable=True)
        raise RuntimeError(
            f"Promoted deployment did not become ready; stable restored:\n"
            f"{(status.stderr or status.stdout or '').strip()}"
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
    prometheus_url: str | None = None,
    wait_for_data: float = 0.0,
) -> Callable[[int, int], bool]:
    """Return an on_step callback that gates each rollout step on Prometheus error rate.

    Returns False (triggering rollback) when the canary backend's 5xx rate
    exceeds config.deploy.error_rate_threshold. "No data" (Prometheus
    unreachable, or no canary traffic yet) passes — metrics down never blocks
    a deploy. prometheus_url defaults to config.deploy.effective_prometheus_url.
    wait_for_data: seconds to keep re-probing while Prometheus answers with no
    data yet (used when test traffic has just started).

    Probes Prometheus once here (just before the rollout starts) and prints a
    single yellow line if the gate has nothing to gate on, so an inactive gate
    is visible without a warning per step.
    """
    from deployguard.engine.metrics import (
        MetricsStatus,
        check_metrics_available,
        get_error_rate,
    )

    if prometheus_url is None:
        prometheus_url = config.deploy.effective_prometheus_url

    status = check_metrics_available(manifest.name, manifest.namespace, prometheus_url)
    # Test traffic was just started: give Prometheus a few scrapes (5s
    # interval on the local install) to see the first requests.
    waited = 0.0
    while status == MetricsStatus.NO_DATA and waited < wait_for_data:
        _sleep(1.0)
        waited += 1.0
        status = check_metrics_available(manifest.name, manifest.namespace, prometheus_url)
    if status == MetricsStatus.UNREACHABLE:
        console.print(
            f"[yellow]Metrics gate inactive: Prometheus not reachable at {prometheus_url}. "
            "Rollout steps will not be gated on error rate.[/yellow]",
            soft_wrap=True,
        )
    elif status == MetricsStatus.NO_DATA:
        console.print(
            f"[yellow]Metrics gate inactive: Prometheus at {prometheus_url} has no "
            f"ingress request data for '{manifest.name}'. Rollout steps will not be "
            "gated on error rate until data appears.[/yellow]",
            soft_wrap=True,
        )

    def _check(step_index: int, weight: int) -> bool:
        rate = get_error_rate(manifest.name, manifest.namespace, prometheus_url)
        if rate is not None and rate > config.deploy.error_rate_threshold:
            console.print(
                f"[bold red]✗[/bold red] Canary error rate [red]{rate:.2f}%[/red] exceeds "
                f"threshold {config.deploy.error_rate_threshold}% at {weight}% traffic — "
                "triggering rollback."
            )
            return False
        return True

    return _check


def rollback(manifest: ServiceManifest, undo_stable: bool = False) -> None:
    """Return all traffic to the stable deployment.

    Deletes the canary Deployment, Service and Ingress (the stable Ingress and
    Service are never touched). During the canary steps the stable Deployment
    has not been modified, so it is left as is: `kubectl rollout undo` there
    would replace the healthy stable version with the one before it. Pass
    undo_stable=True only after the stable Deployment itself was changed
    (a failed promotion).
    """
    _delete_canary(manifest)
    if not undo_stable:
        return
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
    stable_deployment: dict | None = None,
    after_steps: Callable[[], None] | None = None,
) -> bool:
    """Shift traffic through rollout_steps via nginx canary weights.

    after_steps() (optional) is called once the step loop ends, whether it
    passed, failed or raised, and before rollback or promotion starts.

    on_step(step_index, weight) is called after each dwell period.
    Return False from on_step to trigger rollback and return False.
    Returns True when all steps complete and the canary is promoted to stable.
    stable_deployment is the guard-checked Deployment from k8s/ the canary is
    cloned from (None = golden-path template).
    """
    steps = [RolloutStep(weight=w) for w in config.deploy.rollout_steps]

    console.print(f"\n[bold]Rolling out [cyan]{manifest.name}[/cyan][/bold]\n")

    try:
        _apply_canary(manifest, weight=0, target=config.deploy.target, stable=stable_deployment)
    except Exception:
        rollback(manifest)
        raise

    failed = False
    try:
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
                    failed = True
                    break

                step.status = "done"
                live.update(_render_steps(manifest.name, steps))
    finally:
        # Runs before rollback or promotion (and on errors), e.g. to stop
        # test traffic once the gated steps are over.
        if after_steps is not None:
            after_steps()

    if failed:
        rollback(manifest)
        return False

    _promote(manifest)
    _delete_canary(manifest)
    console.print(f"\n[bold green]✓ Rollout complete.[/bold green] "
                  f"Canary promoted to stable.\n")
    return True
