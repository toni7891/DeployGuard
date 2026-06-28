"""AWS provision path — k3s on EC2 Spot via Terraform."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from rich.console import Console

from deployguard.config import AppConfig

console = Console()

_INFRA_DIR = Path(__file__).parent.parent.parent / "infra"
_SSH_KEY = Path.home() / ".ssh" / "deployguard.pem"
_SSH_USER = "ec2-user"
_KUBE_CONTEXT = "deployguard-aws"
_NAMESPACE = "deployguard"


# ── Private helpers (monkeypatch-friendly) ─────────────────────────────────────

def _run(
    cmd: list[str],
    label: str,
    cwd: Path | None = None,
    timeout: int = 600,
    stdin: str | None = None,
) -> subprocess.CompletedProcess:
    with console.status(f"[dim]{label}...[/dim]"):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=timeout,
                input=stdin,
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


def _get_terraform_output(name: str, infra_dir: Path = _INFRA_DIR) -> str:
    if not shutil.which("terraform"):
        raise RuntimeError(
            "terraform not found. Install: brew tap hashicorp/tap && brew install hashicorp/tap/terraform"
        )
    result = subprocess.run(
        ["terraform", "output", "-json", name],
        capture_output=True,
        text=True,
        cwd=infra_dir,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"terraform output '{name}' failed:\n"
            f"{(result.stderr or result.stdout).strip()}"
        )
    return json.loads(result.stdout.strip())


def _wait_for_ssh(host: str, timeout: int = 300) -> None:
    """Poll TCP port 22 until the instance accepts connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, 22), timeout=5):
                return
        except OSError:
            time.sleep(10)
    raise RuntimeError(f"SSH on {host}:22 not available after {timeout}s")


def _fetch_kubeconfig(host: str, eip: str, ssh_key: Path = _SSH_KEY) -> str:
    """SSH to the instance and retrieve the k3s kubeconfig, replacing the loopback address."""
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                "-i", str(ssh_key),
                f"{_SSH_USER}@{host}",
                "cat /home/ec2-user/k3s.yaml",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError("'ssh' not found. Install OpenSSH: brew install openssh")
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to fetch kubeconfig from {host}:\n"
            f"{(result.stderr or result.stdout).strip()}"
        )
    return result.stdout.replace("127.0.0.1", eip).replace("https://127.0.0.1", f"https://{eip}")


def _merge_kubeconfig(kubeconfig_content: str, context_name: str) -> None:
    """Write kubeconfig to ~/.kube/, merge with existing config, set context."""
    if not shutil.which("kubectl"):
        raise RuntimeError("kubectl not found. Install: brew install kubectl")

    kube_dir = Path.home() / ".kube"
    kube_dir.mkdir(parents=True, exist_ok=True)

    tmp = kube_dir / "deployguard-aws-tmp.yaml"
    tmp.write_text(kubeconfig_content)

    existing = kube_dir / "config"
    merge_env = os.environ.copy()
    merge_env["KUBECONFIG"] = f"{existing}:{tmp}" if existing.exists() else str(tmp)

    merged = subprocess.run(
        ["kubectl", "config", "view", "--merge", "--flatten"],
        capture_output=True,
        text=True,
        env=merge_env,
        timeout=15,
    )
    if merged.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"kubeconfig merge failed:\n{merged.stderr.strip()}")

    existing.write_text(merged.stdout)
    tmp.unlink(missing_ok=True)

    # Rename the default k3s context to our canonical name
    subprocess.run(
        ["kubectl", "config", "rename-context", "default", context_name],
        capture_output=True,
        timeout=10,
    )
    subprocess.run(
        ["kubectl", "config", "use-context", context_name],
        capture_output=True,
        timeout=10,
    )


def _setup_cluster() -> None:
    """Install nginx-ingress and create the deployguard namespace (idempotent)."""
    _run(
        ["helm", "repo", "add", "ingress-nginx",
         "https://kubernetes.github.io/ingress-nginx"],
        label="Adding ingress-nginx Helm repo",
        timeout=60,
    )
    _run(["helm", "repo", "update"], label="Updating Helm repos", timeout=60)
    _run(
        [
            "helm", "upgrade", "--install", "ingress-nginx",
            "ingress-nginx/ingress-nginx",
            "--namespace", "ingress-nginx",
            "--create-namespace",
            "--wait",
        ],
        label="Installing nginx-ingress",
        timeout=300,
    )

    ns_result = subprocess.run(
        ["kubectl", "create", "namespace", _NAMESPACE,
         "--dry-run=client", "-o", "yaml"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if ns_result.returncode == 0:
        _run(
            ["kubectl", "apply", "-f", "-"],
            label=f"Applying namespace '{_NAMESPACE}'",
            stdin=ns_result.stdout,
            timeout=30,
        )


# ── Public API ─────────────────────────────────────────────────────────────────

def provision_aws(config: AppConfig, infra_dir: Path = _INFRA_DIR) -> None:
    """Provision k3s on EC2 Spot via Terraform and configure kubectl.

    Steps:
    1. terraform init + apply
    2. Parse EIP from terraform outputs
    3. Wait for SSH, fetch kubeconfig
    4. Merge into ~/.kube/config, set context to 'deployguard-aws'
    5. Install nginx-ingress + create namespace
    """
    console.print("\n[bold]Provisioning AWS cluster (k3s on EC2 Spot)[/bold]\n")

    if not infra_dir.is_dir():
        raise RuntimeError(
            f"Terraform directory '{infra_dir}' not found. "
            "Ensure you are running from the DeployGuard project root."
        )

    # 1. Terraform
    _run(["terraform", "init"], label="terraform init", cwd=infra_dir, timeout=120)
    _run(
        ["terraform", "apply", "-auto-approve"],
        label="terraform apply (this takes ~3 min on first run)",
        cwd=infra_dir,
        timeout=600,
    )

    # 2. EIP
    eip = _get_terraform_output("eip_address", infra_dir)
    console.print(f"[green]✓[/green] Elastic IP: [cyan]{eip}[/cyan]")

    # 3. Wait for instance
    console.print(f"[dim]Waiting for SSH on {eip} (k3s may still be installing)...[/dim]")
    _wait_for_ssh(eip)
    console.print(f"[green]✓[/green] SSH reachable on {eip}")

    # 4. Kubeconfig
    console.print("[dim]Fetching kubeconfig...[/dim]")
    kubeconfig = _fetch_kubeconfig(eip, eip)
    _merge_kubeconfig(kubeconfig, _KUBE_CONTEXT)
    console.print(f"[green]✓[/green] kubectl context → [cyan]{_KUBE_CONTEXT}[/cyan]")

    # 5. In-cluster setup
    _setup_cluster()

    console.print(
        f"\n[bold green]AWS cluster ready.[/bold green] "
        f"Run [cyan]dg deploy[/cyan] to ship.\n"
    )
