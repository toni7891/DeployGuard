import json
import shutil
import subprocess

from deployguard.guard.models import Severity, Violation

_KUBECONFORM_WHY = (
    "Invalid Kubernetes API field — will be silently ignored or rejected by the cluster."
)
_KUBECONFORM_FIX = "See kubeconform output above."


def run_kubeconform(manifest_path: str) -> list[Violation]:
    if not shutil.which("kubeconform"):
        raise RuntimeError(
            "kubeconform not found. Install it and re-run `dg doctor` to verify."
        )

    result = subprocess.run(
        ["kubeconform", "-strict", "-output", "json", manifest_path],
        capture_output=True,
        text=True,
    )

    violations: list[Violation] = []
    # kubeconform emits one JSON object per line
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Each line is {"filename":…,"kind":…,"version":…,"status":…,"msg":…}
        status = obj.get("status", "")
        if status in ("invalid", "error"):
            msg = obj.get("msg", "Schema validation failed.")
            path = obj.get("filename", manifest_path)
            violations.append(
                Violation(
                    rule_id="schema",
                    severity=Severity.ERROR,
                    message=msg,
                    why=_KUBECONFORM_WHY,
                    fix=_KUBECONFORM_FIX,
                    path=path,
                )
            )

    return violations


def run_trivy_config(path: str) -> list[Violation]:
    if not shutil.which("trivy"):
        raise RuntimeError(
            "trivy not found. Install it and re-run `dg doctor` to verify."
        )

    result = subprocess.run(
        ["trivy", "config", "--format", "json", path],
        capture_output=True,
        text=True,
    )

    violations: list[Violation] = []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return violations

    _SEVERITY_MAP = {
        "CRITICAL": Severity.ERROR,
        "HIGH": Severity.ERROR,
        "MEDIUM": Severity.WARN,
        "LOW": Severity.WARN,
        "UNKNOWN": Severity.WARN,
    }

    for result_entry in data.get("Results", []):
        file_path = result_entry.get("Target", path)
        for mis in result_entry.get("Misconfigurations", []):
            raw_severity = mis.get("Severity", "UNKNOWN").upper()
            severity = _SEVERITY_MAP.get(raw_severity, Severity.WARN)
            title = mis.get("Title", "Misconfiguration detected")
            description = mis.get("Description", "")
            resolution = mis.get("Resolution", "See trivy output for details.")
            # Use the trivy check ID as the rule_id prefix so it's identifiable
            check_id = mis.get("ID", "trivy")
            violations.append(
                Violation(
                    rule_id=f"trivy:{check_id}",
                    severity=severity,
                    message=title,
                    why=description or title,
                    fix=resolution,
                    path=file_path,
                )
            )

    return violations
