import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from deployguard.config import get_config, load_config, validate_config_file
from deployguard.engine.deploy import deploy as engine_deploy
from deployguard.manifest import load_manifest
from deployguard.provision.aws import provision_aws
from deployguard.provision.local import provision_local
from deployguard.scaffold.init import scaffold_service

console = Console()

app = typer.Typer(
    name="dg",
    help="DeployGuard — scaffold, validate, cost, provision, and deploy services to Kubernetes.",
    no_args_is_help=True,
)

# ── prerequisite definitions ──────────────────────────────────────────────────

_PREREQS: list[dict] = [
    {
        "name": "Python >= 3.11",
        "check": "python",
        "install": "https://python.org/downloads/ — requires 3.11+",
    },
    {
        "name": "docker",
        "cmd": ["docker", "info"],
        "install": "https://docs.docker.com/get-docker/",
    },
    {
        "name": "kubectl",
        "cmd": ["kubectl", "version", "--client"],
        "install": "brew install kubectl  # or https://kubernetes.io/docs/tasks/tools/",
    },
    {
        "name": "helm",
        "cmd": ["helm", "version"],
        "install": "brew install helm",
    },
    {
        "name": "minikube",
        "cmd": ["minikube", "version"],
        "install": "brew install minikube",
    },
    {
        "name": "kubeconform",
        "cmd": ["kubeconform", "-v"],
        "install": "brew install kubeconform",
    },
    {
        "name": "trivy",
        "cmd": ["trivy", "--version"],
        "install": "brew install aquasecurity/trivy/trivy",
    },
    {
        "name": "terraform",
        "cmd": ["terraform", "version"],
        "install": "brew tap hashicorp/tap && brew install hashicorp/tap/terraform",
    },
    {
        "name": "infracost",
        "cmd": ["infracost", "--version"],
        "install": "brew install infracost",
    },
]


def _check_python() -> tuple[bool, str]:
    vi = sys.version_info
    if vi >= (3, 11):
        return True, f"Python {vi.major}.{vi.minor}.{vi.micro}"
    return False, f"Python {vi.major}.{vi.minor}.{vi.micro} — need >= 3.11"


def _check_cmd(cmd: list[str]) -> tuple[bool, str]:
    binary = cmd[0]
    if not shutil.which(binary):
        return False, f"{binary} not found in PATH"
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            first_line = (result.stdout or result.stderr or "").splitlines()
            version = first_line[0].strip() if first_line else binary
            return True, version
        # Some tools exit non-zero on --version; treat as present if binary exists
        first_line = (result.stdout or result.stderr or "").splitlines()
        return True, (first_line[0].strip() if first_line else binary)
    except subprocess.TimeoutExpired:
        return False, f"{binary} timed out (is the daemon running?)"


# ── commands ──────────────────────────────────────────────────────────────────

@app.command()
def doctor() -> None:
    """Validate prerequisites, config files, and environment."""
    problems: list[str] = []

    # ── 1. Prerequisites ──────────────────────────────────────────────────────
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("status", style="bold", width=3)
    table.add_column("name", width=20)
    table.add_column("detail")

    for prereq in _PREREQS:
        if prereq.get("check") == "python":
            ok, detail = _check_python()
        else:
            ok, detail = _check_cmd(prereq["cmd"])

        if ok:
            table.add_row("[green]✓[/green]", prereq["name"], f"[dim]{detail}[/dim]")
        else:
            problems.append(f"Missing prerequisite: {prereq['name']}")
            table.add_row(
                "[red]✗[/red]",
                prereq["name"],
                f"[red]{detail}[/red]  [dim]→ {prereq['install']}[/dim]",
            )

    console.print()
    console.print("[bold]Prerequisites[/bold]")
    console.print(table)

    # ── 2. Config file validation ─────────────────────────────────────────────
    console.print()
    console.print("[bold]Config files[/bold]")

    personal_path = Path.home() / ".deployguard" / "config.yaml"
    project_path = Path.cwd() / ".deployguard" / "config.yaml"

    for label, path in [("personal", personal_path), ("project", project_path)]:
        if path.exists():
            err = validate_config_file(path)
            if err is None:
                console.print(f"  [green]✓[/green] {path}  [dim]valid[/dim]")
            else:
                console.print(f"  [red]✗[/red] {path}  [red]{err}[/red]")
                problems.append(f"{label} config: {err}")
        else:
            console.print(f"  [dim]–  {path}  not found (optional)[/dim]")

    # Combined load (catches cross-file issues and gives us the merged config)
    config = None
    try:
        config = load_config()
    except (ValidationError, ValueError) as exc:
        short = str(exc).splitlines()[0]
        console.print(f"  [red]✗[/red] Combined config invalid: [red]{short}[/red]")
        problems.append(f"Combined config invalid: {short}")

    # Custom rules dir
    if config and config.guard.custom_rules_dir:
        rules_dir = Path(config.guard.custom_rules_dir)
        if not rules_dir.is_dir():
            console.print(
                f"  [red]✗[/red] custom_rules_dir [cyan]{rules_dir}[/cyan]  "
                "[red]directory not found[/red]"
            )
            problems.append(f"custom_rules_dir not found: {rules_dir}")
        else:
            rule_files = list(rules_dir.glob("*.py")) + list(rules_dir.glob("*.rego"))
            if rule_files:
                console.print(
                    f"  [green]✓[/green] custom_rules_dir [cyan]{rules_dir}[/cyan]  "
                    f"[dim]{len(rule_files)} rule file(s)[/dim]"
                )
            else:
                console.print(
                    f"  [yellow]![/yellow] custom_rules_dir [cyan]{rules_dir}[/cyan]  "
                    "[yellow]exists but no .py/.rego files found[/yellow]"
                )

    # ── 3. deployguard.yaml validation ───────────────────────────────────────
    manifest_path = Path.cwd() / "deployguard.yaml"
    if manifest_path.exists():
        console.print()
        console.print("[bold]deployguard.yaml[/bold]")
        try:
            load_manifest(str(manifest_path))
            console.print(f"  [green]✓[/green] {manifest_path}  [dim]valid ServiceManifest[/dim]")
        except (ValueError, FileNotFoundError) as exc:
            console.print(f"  [red]✗[/red] {manifest_path}  [red]{exc}[/red]")
            problems.append(f"deployguard.yaml: {exc}")

    # ── 4. AWS credentials (only when target is aws) ──────────────────────────
    effective_target = config.deploy.target if config else "local"
    if effective_target == "aws":
        console.print()
        console.print("[bold]AWS credentials[/bold]")
        has_env = bool(
            os.environ.get("AWS_ACCESS_KEY_ID")
            and os.environ.get("AWS_SECRET_ACCESS_KEY")
        )
        has_file = (Path.home() / ".aws" / "credentials").exists()
        if has_env or has_file:
            source = "env vars" if has_env else "~/.aws/credentials"
            console.print(f"  [green]✓[/green] AWS credentials  [dim]{source}[/dim]")
        else:
            console.print(
                "  [red]✗[/red] AWS credentials not found  "
                "[dim]set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY "
                "or run 'aws configure'[/dim]"
            )
            problems.append("AWS credentials not found")

    # ── Summary ───────────────────────────────────────────────────────────────
    console.print()
    if not problems:
        console.print("[bold green]All checks passed.[/bold green]")
        raise typer.Exit(0)
    else:
        console.print(f"[bold red]{len(problems)} problem(s) found:[/bold red]")
        for p in problems:
            console.print(f"  [red]•[/red] {p}")
        raise typer.Exit(1)


@app.command()
def init(
    name: Annotated[str, typer.Argument(help="Service name")],
    no_guard: Annotated[
        bool,
        typer.Option("--no-guard", help="Skip guard validation (escape hatch — never use in CI)"),
    ] = False,
) -> None:
    """Scaffold a hardened FastAPI+Postgres golden-path service."""
    config = get_config()
    try:
        scaffold_service(name, ".", config, no_guard=no_guard)
    except (ValueError, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def cost(
    path: Annotated[str, typer.Argument(help="Path to infra/ directory or Terraform root")] = ".",
    explain: Annotated[
        bool,
        typer.Option("--explain", help="Show verbose reasoning per cost line"),
    ] = False,
) -> None:
    """Static cost report: what spins up, estimated monthly cost, bill-inflation risks."""
    from deployguard.cost import (
        format_cost_report,
        parse_k8s_resources,
        parse_terraform_resources,
        run_cost_rules,
        run_infracost,
    )

    config = get_config()

    tf_resources = parse_terraform_resources(path)
    # k8s resources: look in path/k8s, fall back to parent/k8s
    k8s_candidate = Path(path) / "k8s"
    if not k8s_candidate.is_dir():
        k8s_candidate = Path(path).parent / "k8s"
    k8s_resources = parse_k8s_resources(str(k8s_candidate))

    infracost_result: dict = {}
    try:
        infracost_result = run_infracost(path)
    except RuntimeError as exc:
        console.print(f"[yellow]![/yellow] {exc} — cost estimates will be omitted.")

    violations = run_cost_rules(tf_resources + k8s_resources)
    console.print(format_cost_report(tf_resources, k8s_resources, infracost_result, violations, explain=explain))

    # Threshold enforcement
    has_reject = any(v.severity == "REJECT" for v in violations)
    total_cost: float | None = None
    if infracost_result:
        raw = infracost_result.get("totalMonthlyCost")
        if raw is None:
            projects = infracost_result.get("projects", [])
            if projects:
                raw = projects[0].get("breakdown", {}).get("totalMonthlyCost")
        try:
            total_cost = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            pass

    if has_reject:
        console.print(
            "[bold red]✗ Cost guard REJECTED — remove the violations above before provisioning.[/bold red]"
        )
        raise typer.Exit(1)

    if total_cost is not None:
        if total_cost >= config.cost.reject_threshold:
            console.print(
                f"[bold red]✗ Estimated ${total_cost:.2f}/mo exceeds reject threshold "
                f"${config.cost.reject_threshold:.2f}/mo.[/bold red]"
            )
            raise typer.Exit(1)
        elif total_cost >= config.cost.warn_threshold:
            console.print(
                f"[yellow]! Estimated ${total_cost:.2f}/mo exceeds warn threshold "
                f"${config.cost.warn_threshold:.2f}/mo.[/yellow]"
            )


@app.command()
def provision(
    target: Annotated[
        str | None,
        typer.Option("--target", help="Override config target for this run (local|aws)"),
    ] = None,
) -> None:
    """Ensure a configured cluster exists (local k3d or aws k3s)."""
    config = get_config()
    effective_target = target or config.deploy.target
    try:
        if effective_target == "local":
            provision_local(config)
        else:
            provision_aws(config)
    except RuntimeError as exc:
        console.print(f"\n[red]Provision failed:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def deploy(
    target: Annotated[
        str | None,
        typer.Option("--target", help="Override config target for this run (local|aws)"),
    ] = None,
) -> None:
    """Build → push → safe rollout with health checks and auto-rollback."""
    config = get_config()
    if target:
        config = config.model_copy(update={"deploy": config.deploy.model_copy(update={"target": target})})
    try:
        manifest = load_manifest("deployguard.yaml")
        success = engine_deploy(manifest, config)
        if not success:
            raise typer.Exit(1)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)
    except RuntimeError as exc:
        console.print(f"\n[red]Deploy failed:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def dashboard(
    url_only: Annotated[
        bool,
        typer.Option("--url", help="Print dashboard URL instead of opening browser"),
    ] = False,
) -> None:
    """Open the minikube Kubernetes dashboard in the browser."""
    if not shutil.which("minikube"):
        console.print("[red]Error:[/red] minikube not found. Run `dg doctor`.")
        raise typer.Exit(1)
    cmd = ["minikube", "dashboard", "--profile", "deployguard"]
    if url_only:
        cmd.append("--url")
    else:
        console.print("[dim]Opening Kubernetes dashboard... (Ctrl-C to stop)[/dim]")
    subprocess.run(cmd)
