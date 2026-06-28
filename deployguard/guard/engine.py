from deployguard.guard.models import Severity, Violation
from deployguard.guard.rules import ALL_RULES

_SEVERITY_MAP = {
    "error": Severity.ERROR,
    "warn": Severity.WARN,
}


def run_policy(manifest: dict, enabled_rules: dict[str, str]) -> list[Violation]:
    """Run all enabled policy rules against a single manifest dict.

    enabled_rules maps rule_id → "error" | "warn" | "off".
    Severity in the returned violations is set from enabled_rules, overriding
    whatever the rule function returns.
    """
    violations: list[Violation] = []

    for rule_id, rule_fn in ALL_RULES:
        level = enabled_rules.get(rule_id, "error")
        if level == "off":
            continue

        raw = rule_fn(manifest)
        severity = _SEVERITY_MAP.get(level, Severity.ERROR)

        for v in raw:
            violations.append(v.model_copy(update={"severity": severity}))

    return violations
