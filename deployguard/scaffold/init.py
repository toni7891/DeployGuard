from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from rich.console import Console

from deployguard.config import AppConfig
from deployguard.guard import GuardResult, Severity, format_result, guard
from deployguard.llm import get_adapter
from deployguard.manifest import ServiceManifest

console = Console()

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"

_TEMPLATES: list[tuple[str, str]] = [
    ("Dockerfile.j2", "Dockerfile"),
    ("app/main.py.j2", "app/main.py"),
    ("app/requirements.txt.j2", "app/requirements.txt"),
    ("k8s/deployment.yaml.j2", "k8s/deployment.yaml"),
    ("k8s/service.yaml.j2", "k8s/service.yaml"),
    ("k8s/serviceaccount.yaml.j2", "k8s/serviceaccount.yaml"),
    ("deployguard.yaml.j2", "deployguard.yaml"),
]

# Files the LLM is allowed to draft instead of Jinja2 — only ones that benefit
# from LLM flexibility. Guard always has final say over what lands on disk.
_LLM_CONTENT_SCHEMA = {
    "type": "object",
    "properties": {"content": {"type": "string"}},
    "required": ["content"],
    "additionalProperties": False,
}


def _dockerfile_prompt(ctx: dict) -> str:
    return (
        f"Write a production Dockerfile for a Python 3.11 FastAPI service named "
        f"'{ctx['name']}' listening on port {ctx['port']}.\n"
        "Requirements:\n"
        "- Base image python:3.11-slim, tag pinned (never 'latest')\n"
        "- Copy app/requirements.txt and pip install before copying the rest of "
        "app/ (layer caching)\n"
        "- Copy app/ into /app\n"
        f"- EXPOSE {ctx['port']}\n"
        f"- CMD runs: uvicorn main:app --host 0.0.0.0 --port {ctx['port']}\n"
        'Return ONLY a JSON object: {"content": "<full Dockerfile text>"}.'
    )


def _deployment_prompt(ctx: dict, feedback: str | None = None) -> str:
    prompt = (
        "Write a Kubernetes Deployment manifest (apiVersion: apps/v1) for a "
        f"service named '{ctx['name']}' in namespace '{ctx['namespace']}'.\n"
        "Requirements:\n"
        f"- replicas: {ctx['replicas']}\n"
        f"- exactly one container, containerPort {ctx['port']}, image tag pinned "
        "(never 'latest')\n"
        "- pod securityContext.runAsNonRoot: true\n"
        "- container securityContext.allowPrivilegeEscalation: false, "
        "readOnlyRootFilesystem: true, and capabilities.drop: [ALL]\n"
        "- resources.requests AND resources.limits set for both cpu and memory\n"
        f"- livenessProbe: httpGet path {ctx['health_liveness']}, port {ctx['port']}\n"
        f"- readinessProbe: httpGet path {ctx['health_readiness']}, port {ctx['port']}\n"
        'Return ONLY a JSON object: {"content": "<full YAML manifest text>"}.'
    )
    if feedback:
        prompt += (
            "\n\nThe previous draft failed validation. Fix every issue below and "
            "return a corrected manifest:\n" + feedback
        )
    return prompt


def _violations_feedback(result: GuardResult) -> str:
    return "\n".join(
        f"- [{v.rule_id}] {v.message} (why: {v.why}; fix: {v.fix})"
        for v in result.violations
        if v.severity == Severity.ERROR
    )


def _guard_check_content(content: str, filename: str, config: AppConfig) -> GuardResult:
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, filename).write_text(content)
        return guard(
            tmp, config.guard.rules.as_dict(), run_kubeconform=True, run_trivy=True
        )


def _try_llm_draft(ctx: dict, config: AppConfig) -> dict[str, str]:
    """Attempt to draft Dockerfile + deployment.yaml via the configured LLM backend.

    Returns {} (meaning: use the built-in Jinja2 templates for these files) if
    the backend isn't reachable, errors out, or its draft still fails guard
    after one retry. `dg init` always succeeds either way.
    """
    adapter = get_adapter(config)

    console.print("[dim]Checking LLM backend availability...[/dim]")
    if not adapter.is_available():
        console.print(
            "[yellow]![/yellow] LLM backend not reachable — using built-in templates. "
            "(Start LM Studio's local server on port 1234 to enable LLM-drafted manifests.)"
        )
        return {}

    console.print(
        f"[cyan]✓[/cyan] LLM backend detected — drafting Dockerfile + "
        f"deployment.yaml with [bold]{adapter.model}[/bold]"
    )

    try:
        dockerfile_content = adapter.generate(
            _dockerfile_prompt(ctx), _LLM_CONTENT_SCHEMA
        )["content"]
        deployment_content = adapter.generate(
            _deployment_prompt(ctx), _LLM_CONTENT_SCHEMA
        )["content"]
    except (RuntimeError, KeyError, ValueError) as exc:
        console.print(f"[yellow]![/yellow] LLM draft failed ({exc}) — using built-in templates.")
        return {}

    result = _guard_check_content(deployment_content, "deployment.yaml", config)
    if result.has_errors:
        console.print("[yellow]![/yellow] LLM draft failed guard — retrying with feedback...")
        feedback = _violations_feedback(result)
        try:
            deployment_content = adapter.generate(
                _deployment_prompt(ctx, feedback), _LLM_CONTENT_SCHEMA
            )["content"]
        except (RuntimeError, KeyError, ValueError) as exc:
            console.print(f"[yellow]![/yellow] LLM retry failed ({exc}) — using built-in templates.")
            return {}

        result = _guard_check_content(deployment_content, "deployment.yaml", config)
        if result.has_errors:
            console.print(
                "[yellow]![/yellow] LLM draft failed guard twice — using built-in templates."
            )
            return {}

    console.print("[green]✓[/green] LLM draft passed guard.")
    return {"Dockerfile": dockerfile_content, "k8s/deployment.yaml": deployment_content}


def scaffold_service(
    name: str, output_dir: str, config: AppConfig, no_guard: bool = False
) -> None:
    manifest = ServiceManifest(name=name)
    ctx = {
        "name": manifest.name,
        "port": manifest.port,
        "replicas": manifest.replicas,
        "health_liveness": manifest.health_liveness,
        "health_readiness": manifest.health_readiness,
        "namespace": manifest.namespace,
    }

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )

    service_dir = Path(output_dir) / name

    # Optional LLM-drafted path — only attempted if llm.backend is configured.
    # Falls back to Jinja2 templates (the default path below) on any failure.
    llm_drafted: dict[str, str] = {}
    if config.llm.backend is not None:
        llm_drafted = _try_llm_draft(ctx, config)
        console.print()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_service = Path(tmp) / name

        # Render all templates into the temp directory first
        for tmpl_path, out_rel in _TEMPLATES:
            if out_rel in llm_drafted:
                rendered = llm_drafted[out_rel]
            else:
                template = env.get_template(tmpl_path)
                rendered = template.render(**ctx)
            out_path = tmp_service / out_rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(rendered)

        # Guard stage — runs on rendered files before they land in the project
        if no_guard:
            console.print(
                "[yellow]Warning: guard skipped (--no-guard). Never use in CI.[/yellow]"
            )
        else:
            k8s_dir = tmp_service / "k8s"
            result = guard(
                str(k8s_dir),
                config.guard.rules.as_dict(),
                run_kubeconform=True,
                run_trivy=True,
            )

            if result.violations:
                console.print(format_result(result, explain=config.guard.explain))

            if result.has_errors and config.guard.strict:
                console.print(
                    "[red]Guard failed — files not written. Fix violations above.[/red]"
                )
                sys.exit(1)

        # Guard passed (or skipped) — copy rendered files to final destination
        shutil.copytree(str(tmp_service), str(service_dir), dirs_exist_ok=True)

    written = [service_dir / out_rel for _, out_rel in _TEMPLATES]
    for out_path in written:
        console.print(f"  [green]created[/green] {out_path}")

    console.print()
    console.print(
        f"[bold green]✓[/bold green] Scaffolded [bold]{name}[/bold] "
        f"— {len(written)} files in [dim]{service_dir}/[/dim]"
    )
    console.print()
    console.print("Next steps:")
    console.print(f"  [cyan]dg cost[/cyan]")
    console.print(f"  [cyan]dg provision[/cyan]")
    console.print(f"  [cyan]dg deploy[/cyan]")
