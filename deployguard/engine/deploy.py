"""Full deploy sequence: build → load/push → pre-check → gradual rollout → audit."""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import boto3
import yaml
from rich.console import Console

from deployguard.config import AppConfig
from deployguard.engine.audit import try_write_audit
from deployguard.engine.ecr import push_to_ecr
from deployguard.engine.metrics import get_error_rate
from deployguard.engine.portforward import prometheus_endpoint
from deployguard.engine.precheck import run_precheck
from deployguard.engine.rollout import make_prometheus_checker, rollback, rollout_traffic
from deployguard.engine.traffic import smoke_traffic
from deployguard.guard import format_result, guard
from deployguard.manifest import ServiceManifest

console = Console()

_CLUSTER_NAME = "deployguard"
# How long to wait for the first test requests to show up in Prometheus
# (5s scrape interval on the local install) before calling the gate inactive.
_METRICS_WARMUP_SECONDS = 20.0


# ── Private helpers (monkeypatch-friendly) ─────────────────────────────────────

def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "local"


_BUILD_LOG_TAIL = 40


def _run_docker_build(image_ref: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "build", "-t", image_ref, "."],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _build_image(name: str, tag: str) -> None:
    """docker build with a single spinner line; the log is shown only on failure."""
    if not shutil.which("docker"):
        raise RuntimeError(
            "docker not found. Install Docker Desktop and run `dg doctor` to verify."
        )
    image_ref = f"{name}:{tag}"
    with console.status(f"[dim]Building {image_ref}...[/dim]"):
        try:
            result = _run_docker_build(image_ref)
        except FileNotFoundError:
            raise RuntimeError(
                "docker not found. Install Docker Desktop and run `dg doctor` to verify."
            )
    if result.returncode != 0:
        tail = "\n".join((result.stdout or "").rstrip().splitlines()[-_BUILD_LOG_TAIL:])
        console.print(f"[red]✗[/red] Build {image_ref} failed")
        if tail:
            console.print(tail, markup=False, highlight=False)
        raise RuntimeError(f"docker build failed (exit {result.returncode}):\n{tail}")
    console.print(f"[green]✓[/green] Built {image_ref}")


def _load_image_local(image_ref: str) -> None:
    if not shutil.which("minikube"):
        raise RuntimeError("minikube not found. Install: brew install minikube")
    with console.status(f"[dim]Loading {image_ref} into minikube...[/dim]"):
        try:
            result = subprocess.run(
                ["minikube", "image", "load", image_ref, "--profile", _CLUSTER_NAME],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError:
            raise RuntimeError("minikube not found. Install: brew install minikube")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"minikube image load failed:\n{detail}")
    console.print(f"[green]✓[/green] Loaded {image_ref} into minikube cluster")


def _aws_region() -> str:
    session = boto3.Session()
    return session.region_name or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def _aws_account_id(region: str) -> str:
    return boto3.client("sts", region_name=region).get_caller_identity()["Account"]


def _stable_deployment_exists(name: str, namespace: str) -> bool:
    result = subprocess.run(
        ["kubectl", "get", "deployment", name, "-n", namespace],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode == 0


def _load_k8s_docs(k8s_dir: str) -> list[dict]:
    docs: list[dict] = []
    for path in sorted(Path(k8s_dir).glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict):
                docs.append(doc)
    return docs


def _stable_deployment_doc(k8s_dir: str, name: str) -> dict | None:
    """The guard-checked stable Deployment from k8s/, which the canary is cloned from."""
    for doc in _load_k8s_docs(k8s_dir):
        if doc.get("kind") == "Deployment" and (doc.get("metadata") or {}).get("name") == name:
            return doc
    return None


def _apply_stable(manifest: ServiceManifest, k8s_dir: str, target: str) -> None:
    """Apply every k8s/*.yaml (Deployment, Service, ServiceAccount, Ingress) with
    the new image as the stable deployment (first-deploy path)."""
    docs: list[dict] = []
    for doc in _load_k8s_docs(k8s_dir):
        if doc.get("kind") == "Deployment":
            containers = (
                doc.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("containers", [])
            )
            for c in containers:
                c["image"] = manifest.image or manifest.name
                if target == "local":
                    c["imagePullPolicy"] = "Never"
        docs.append(doc)

    combined = yaml.dump_all(docs, default_flow_style=False)
    apply = subprocess.run(
        ["kubectl", "apply", "-f", "-", "-n", manifest.namespace],
        input=combined,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if apply.returncode != 0:
        raise RuntimeError(
            f"kubectl apply failed:\n{(apply.stderr or apply.stdout or '').strip()}"
        )
    rollout = subprocess.run(
        [
            "kubectl", "rollout", "status",
            f"deployment/{manifest.name}",
            "-n", manifest.namespace,
            "--timeout=120s",
        ],
        capture_output=True,
        text=True,
        timeout=130,
    )
    if rollout.returncode != 0:
        raise RuntimeError(
            f"Rollout did not complete:\n{(rollout.stderr or rollout.stdout or '').strip()}"
        )


def _push_image_ecr(image_ref: str) -> None:
    name, tag = image_ref.rsplit(":", 1)
    region = _aws_region()
    account_id = _aws_account_id(region)
    uri = push_to_ecr(name, tag, region, account_id)
    console.print(f"[green]✓[/green] Pushed to ECR: {uri}")


# ── Public API ─────────────────────────────────────────────────────────────────

def deploy(manifest: ServiceManifest, config: AppConfig) -> bool:
    """Run the full DeployGuard deploy sequence.

    Returns True on successful promotion, False if pre-check failed or rollback
    was triggered (either by Prometheus threshold or by the on_step callback).
    On unhandled exceptions: calls rollback() (removes canary resources) then re-raises.
    """
    target = config.deploy.target
    database_url = os.environ.get("DATABASE_URL") if config.deploy.audit else None
    started_at = datetime.now(timezone.utc)

    # 1. Tag image with git SHA
    image_tag = _git_sha()
    image_ref = f"{manifest.name}:{image_tag}"
    manifest = manifest.model_copy(update={"image": image_ref})

    console.print(
        f"\n[bold]dg deploy — [cyan]{manifest.name}[/cyan][/bold]"
        f"  target=[cyan]{target}[/cyan]  tag=[dim]{image_tag}[/dim]\n"
    )

    try:
        # 2. Guard — validate manifests before touching the cluster
        console.print("\n[bold]Guard[/bold]")
        k8s_dir = os.path.join(os.getcwd(), "k8s")
        guard_result = guard(
            k8s_dir,
            config.guard.rules.as_dict(),
            run_kubeconform=True,
            run_trivy=True,
        )
        if guard_result.violations:
            console.print(format_result(guard_result, explain=config.guard.explain))
        if guard_result.has_errors and config.guard.strict:
            console.print("[red]✗ Guard failed — deploy aborted.[/red]")
            try_write_audit(
                database_url,
                service=manifest.name,
                image_tag=image_tag,
                target=target,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                result="precheck_failed",
                reason="Guard violations blocked deploy",
            )
            return False
        console.print("[green]✓ Guard passed.[/green]\n")

        # 3. Build
        _build_image(manifest.name, image_tag)

        # 4. Load / push
        if target == "local":
            _load_image_local(image_ref)
        else:
            _push_image_ecr(image_ref)

        # 5. Pre-check — abort before touching live traffic on failure
        console.print("\n[bold]Pre-check[/bold]")
        precheck = run_precheck(manifest, config)
        if not precheck.passed:
            console.print(f"[red]✗ Pre-check failed:[/red] {precheck.reason}")
            try_write_audit(
                database_url,
                service=manifest.name,
                image_tag=image_tag,
                target=target,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                result="precheck_failed",
                reason=precheck.reason,
            )
            return False
        console.print("[green]✓ Pre-check passed.[/green]\n")

        # 6. First-deploy: stable deployment doesn't exist yet — apply directly
        if not _stable_deployment_exists(manifest.name, manifest.namespace):
            console.print("[bold]First deploy — applying stable deployment directly.[/bold]")
            _apply_stable(manifest, k8s_dir, target)
            console.print("[bold green]✓ First deploy complete.[/bold green]\n")
            success = True
        else:
            # 6b. Gradual rollout with Prometheus error-rate gate. On the local
            # target both context managers port-forward into the cluster
            # (Prometheus, ingress) and always clean up on exit; test traffic
            # is stopped by rollout_traffic before rollback/promotion.
            with prometheus_endpoint(config) as prom_url, \
                    smoke_traffic(manifest, config) as traffic:
                on_step = make_prometheus_checker(
                    manifest,
                    config,
                    prometheus_url=prom_url,
                    wait_for_data=_METRICS_WARMUP_SECONDS if traffic else 0.0,
                )
                success = rollout_traffic(
                    manifest,
                    config,
                    on_step=on_step,
                    stable_deployment=_stable_deployment_doc(k8s_dir, manifest.name),
                    after_steps=traffic.stop if traffic else None,
                )

        if not success:
            console.print(
                "\n[bold red]Rolled back.[/bold red] "
                "Live traffic unaffected."
            )
            try_write_audit(
                database_url,
                service=manifest.name,
                image_tag=image_tag,
                target=target,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                result="rollback",
                reason=f"Error rate exceeded {config.deploy.error_rate_threshold}% threshold",
            )
            return False

        console.print(
            "\n[bold green]Deploy complete.[/bold green] "
            "100% traffic on new version."
        )
        try_write_audit(
            database_url,
            service=manifest.name,
            image_tag=image_tag,
            target=target,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            result="success",
        )
        return True

    except Exception:
        rollback(manifest)
        try_write_audit(
            database_url,
            service=manifest.name,
            image_tag=image_tag,
            target=target,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            result="precheck_failed",
            reason="Unexpected exception — see logs",
        )
        raise
