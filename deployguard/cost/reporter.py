"""Rich-formatted three-section cost report."""
from __future__ import annotations

from rich.console import Group
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from deployguard.cost.rules import CostViolation


def _resources_table(tf_resources: list[dict], k8s_resources: list[dict]) -> Table:
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Type / Kind", style="cyan")
    table.add_column("Name")
    table.add_column("Detail", style="dim")

    for r in tf_resources:
        cfg = r.get("config", {})
        detail = (
            cfg.get("instance_type")
            or cfg.get("engine")
            or (f"{cfg.get('allocated_storage')}GB" if cfg.get("allocated_storage") else "")
            or ""
        )
        table.add_row(r["type"], r["name"], str(detail))

    for r in k8s_resources:
        detail = ""
        if r.get("replicas") is not None:
            detail = f"replicas={r['replicas']}"
        if r.get("storage"):
            detail = f"storage={r['storage']}"
        table.add_row(r["kind"], r["name"], detail)

    if not tf_resources and not k8s_resources:
        table.add_row("[dim]no resources found[/dim]", "", "")

    return table


def _cost_table(infracost_result: dict) -> Table:
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Scenario", style="bold")
    table.add_column("Est. monthly cost", style="green")
    table.add_column("Source", style="dim")

    if not infracost_result:
        table.add_row(
            "[dim]not available[/dim]",
            "[dim]—[/dim]",
            "infracost not run (no .tf files or tool missing)",
        )
        return table

    # infracost JSON shape: projects[].breakdown.totalMonthlyCost
    total = infracost_result.get("totalMonthlyCost")
    if total is None:
        projects = infracost_result.get("projects", [])
        if projects:
            total = projects[0].get("breakdown", {}).get("totalMonthlyCost")

    if total is not None:
        table.add_row("Running", f"${float(total):.2f}", "infracost")
    else:
        table.add_row("[dim]cost data unavailable[/dim]", "[dim]—[/dim]", "infracost")

    return table


def _violations_table(violations: list[CostViolation], explain: bool) -> Table:
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Severity", width=8)
    table.add_column("Rule", style="cyan")
    table.add_column("Finding")
    if explain:
        table.add_column("Why / Impact")

    if not violations:
        row = ["[green]none[/green]", "", "no bill-inflation risks found"]
        if explain:
            row.append("")
        table.add_row(*row)
        return table

    for v in violations:
        sev = (
            "[bold red]REJECT[/bold red]"
            if v.severity == "REJECT"
            else "[bold yellow]WARN[/bold yellow]"
        )
        msg = v.message
        if v.monthly_impact_estimate:
            msg += f"  [dim]{v.monthly_impact_estimate}[/dim]"
        row = [sev, v.rule_id, msg]
        if explain:
            row.append(v.why)
        table.add_row(*row)

    return table


def format_cost_report(
    tf_resources: list[dict],
    k8s_resources: list[dict],
    infracost_result: dict,
    violations: list[CostViolation],
    explain: bool = False,
) -> Group:
    """Return a Rich renderable group for console.print()."""
    return Group(
        Text(""),
        Rule("[bold]What will spin up[/bold]"),
        _resources_table(tf_resources, k8s_resources),
        Text(""),
        Rule("[bold]Estimated monthly cost[/bold]"),
        _cost_table(infracost_result),
        Text(""),
        Rule("[bold]Bill-inflation risks[/bold]"),
        _violations_table(violations, explain),
        Text(""),
        Text(
            "This is an estimate. Actual costs depend on usage.",
            style="dim italic",
        ),
    )
