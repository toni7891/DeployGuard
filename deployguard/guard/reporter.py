from deployguard.guard.models import GuardResult, Severity


def format_result(result: GuardResult, explain: bool = True) -> str:
    lines: list[str] = []

    for v in result.violations:
        if v.severity == Severity.ERROR:
            tag = "[bold red]ERROR[/bold red]"
        else:
            tag = "[bold yellow]WARN[/bold yellow]"

        location = f" in {v.path}" if v.path else ""
        lines.append(f"{tag} [{v.rule_id}]{location}")
        lines.append(f"  {v.message}")

        if explain:
            lines.append(f"  [dim]Why it matters:[/dim] {v.why}")
            lines.append(f"  [dim]What to add:[/dim] {v.fix}")

        lines.append("")

    error_count = sum(1 for v in result.violations if v.severity == Severity.ERROR)
    warn_count = sum(1 for v in result.violations if v.severity == Severity.WARN)

    if result.passed:
        summary_color = "green"
        outcome = "guard passed"
    else:
        summary_color = "red"
        outcome = "guard failed"

    lines.append(
        f"[{summary_color}]{error_count} error(s), {warn_count} warning(s) — {outcome}.[/{summary_color}]"
    )

    return "\n".join(lines)
