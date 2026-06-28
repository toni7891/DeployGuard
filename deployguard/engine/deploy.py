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
from deployguard.engine.precheck import run_precheck
from deployguard.engine.rollout import make_prometheus_checker, rollback, rollout_traffic
from deployguard.guard import format_result, guard
from deployguard.manifest import ServiceManifest

console = Console()

_CLUSTER_NAME = "deployguard"


# ── Private helpers (monkeypatch-friendly) ─────────────────────────────────────

def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "local"


def _build_image(name: str, tag: str) -> None:
    if not shutil.which("docker"):
        raise RuntimeError(
            "docker not found. Install Docker Desktop and run `dg doctor` to verify."
        )
    image_ref = f"{name}:{tag}"
    console.print(f"[dim]Building {image_ref}...[/dim]")
    try:
        proc_ctx = subprocess.Popen(
            ["docker", "build", "-t", image_ref, "."],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "docker not found. Install Docker Desktop and run `dg doctor` to verify."
        )
    with proc_ctx as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            console.print(f"  [dim]{line.rstrip()}[/dim]")
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"docker build failed (exit {proc.returncode})")
    console.print(f"[green]✓[/green] Built {image_ref}")


def _load_image_local(image_ref: str) -> None:
    if not shutil.which("minikube"):
        raise RuntimeError("minikube not found. Install: brew install minikube")
    console.print(f"[dim]Loading {image_ref} into minikube...[/dim]")
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


def _apply_stable(manifest: ServiceManifest, k8s_dir: str, target: str) -> None:
    """Apply k8s manifests with the new image as the stable deployment (first-deploy path)."""
    docs: list[dict] = []
    for path in sorted(Path(k8s_dir).glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if doc is None:
                continue
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
    On unhandled exceptions: calls rollback() then re-raises.
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
            # 6b. Gradual rollout with Prometheus error-rate gate
            on_step = make_prometheus_checker(manifest, config)
            success = rollout_traffic(manifest, config, on_step=on_step)

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
