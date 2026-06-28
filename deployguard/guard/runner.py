import os
from pathlib import Path

import yaml

from deployguard.guard.engine import run_policy
from deployguard.guard.models import GuardResult, Severity, Violation
from deployguard.guard.tools import (
    run_kubeconform as _tool_kubeconform,
    run_trivy_config as _tool_trivy,
)


def _yaml_files(path: str) -> list[Path]:
    p = Path(path)
    if p.is_file():
        return [p]
    return sorted(p.rglob("*.yaml")) + sorted(p.rglob("*.yml"))


def _parse_yaml_file(file_path: Path) -> tuple[list[dict], list[Violation]]:
    """Return (manifests, parse_error_violations)."""
    try:
        text = file_path.read_text()
        docs = [d for d in yaml.safe_load_all(text) if d is not None]
        return docs, []
    except yaml.YAMLError as exc:
        return [], [
            Violation(
                rule_id="yaml_parse",
                severity=Severity.ERROR,
                message=f"YAML parse error: {exc}",
                why="A malformed YAML file will be rejected by kubectl and kubeconform.",
                fix="Fix the YAML syntax error indicated above.",
                path=str(file_path),
            )
        ]


def guard(
    path: str,
    enabled_rules: dict[str, str],
    run_kubeconform: bool = True,
    run_trivy: bool = True,
) -> GuardResult:
    files = _yaml_files(path)
    all_violations: list[Violation] = []

    for file_path in files:
        manifests, parse_errors = _parse_yaml_file(file_path)
        all_violations.extend(parse_errors)
        if parse_errors:
            continue

        # External tool checks (per file, so violation paths are accurate)
        # RuntimeError means the binary is missing — emit WARN and continue.
        if run_kubeconform:
            try:
                all_violations.extend(_tool_kubeconform(str(file_path)))
            except RuntimeError as exc:
                all_violations.append(Violation(
                    rule_id="tool_missing",
                    severity=Severity.WARN,
                    message=str(exc),
                    why="External tools provide schema and CVE checks beyond Python policy rules.",
                    fix="Run 'dg doctor' to see which prerequisites are missing.",
                    path=str(file_path),
                ))

        if run_trivy:
            try:
                all_violations.extend(_tool_trivy(str(file_path)))
            except RuntimeError as exc:
                all_violations.append(Violation(
                    rule_id="tool_missing",
                    severity=Severity.WARN,
                    message=str(exc),
                    why="External tools provide schema and CVE checks beyond Python policy rules.",
                    fix="Run 'dg doctor' to see which prerequisites are missing.",
                    path=str(file_path),
                ))

        # Policy rule checks (per manifest document)
        for manifest in manifests:
            if not isinstance(manifest, dict):
                continue
            policy_v = run_policy(manifest, enabled_rules)
            for v in policy_v:
                all_violations.extend(
                    [v.model_copy(update={"path": v.path or str(file_path)})]
                )

    passed = not any(v.severity == Severity.ERROR for v in all_violations)
    return GuardResult(passed=passed, violations=all_violations)
