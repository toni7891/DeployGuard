"""Cost policy rules — warn / reject on bill-inflation footguns."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

# Resource types that accrue standing cost 24/7
_STANDING_COST_TYPES = frozenset(
    {
        "aws_instance",
        "aws_db_instance",
        "aws_eip",
        "aws_lb",
        "aws_alb",
        "aws_nat_gateway",
        "aws_elasticache_cluster",
        "aws_elasticsearch_domain",
    }
)

# Instance types within the golden-path size budget
_SMALL_INSTANCE_TYPES = frozenset(
    {"t3.nano", "t3.micro", "t3.small", "t2.nano", "t2.micro", "t2.small"}
)


class CostViolation(BaseModel):
    rule_id: str
    severity: Literal["WARN", "REJECT"]
    message: str
    why: str
    monthly_impact_estimate: str | None = None


# ── Rules ─────────────────────────────────────────────────────────────────────


def rule_nat_gateway(resources: list[dict]) -> list[CostViolation]:
    return [
        CostViolation(
            rule_id="nat_gateway",
            severity="REJECT",
            message=f"NAT Gateway '{r['name']}' found.",
            why=(
                "A NAT Gateway costs ~$32/mo in base fees plus data-processing charges. "
                "DeployGuard's golden path uses a public subnet with an Internet Gateway — "
                "no NAT required. Remove it."
            ),
            monthly_impact_estimate="~$32+/mo",
        )
        for r in resources
        if r.get("type") == "aws_nat_gateway"
    ]


def rule_on_demand_instance(resources: list[dict]) -> list[CostViolation]:
    violations = []
    for r in resources:
        if r.get("type") != "aws_instance":
            continue
        cfg = r.get("config", {})
        is_spot = cfg.get("spot_price") is not None or cfg.get("market_type") == "spot"
        if not is_spot:
            violations.append(
                CostViolation(
                    rule_id="on_demand_instance",
                    severity="WARN",
                    message=f"EC2 instance '{r['name']}' is not using Spot pricing.",
                    why=(
                        "On-demand EC2 instances cost 3–5× more than Spot for the same "
                        "type. The golden path uses Spot to hit the ~$6/mo running target."
                    ),
                    monthly_impact_estimate="varies",
                )
            )
    return violations


def rule_oversized_instance(resources: list[dict]) -> list[CostViolation]:
    violations = []
    for r in resources:
        if r.get("type") != "aws_instance":
            continue
        instance_type = r.get("config", {}).get("instance_type", "")
        if instance_type and instance_type not in _SMALL_INSTANCE_TYPES:
            violations.append(
                CostViolation(
                    rule_id="oversized_instance",
                    severity="WARN",
                    message=(
                        f"EC2 instance '{r['name']}' uses '{instance_type}' "
                        "— larger than the golden-path budget (t3.small or smaller)."
                    ),
                    why=(
                        "The golden path targets t3.small or smaller to stay within "
                        "the ~$6/mo running budget. Larger instances break that target."
                    ),
                    monthly_impact_estimate="varies",
                )
            )
    return violations


def rule_unattached_eip(resources: list[dict]) -> list[CostViolation]:
    violations = []
    for r in resources:
        if r.get("type") != "aws_eip":
            continue
        cfg = r.get("config", {})
        attached = cfg.get("association_id") or cfg.get("instance") or cfg.get("network_interface")
        if not attached:
            violations.append(
                CostViolation(
                    rule_id="unattached_eip",
                    severity="WARN",
                    message=f"Elastic IP '{r['name']}' has no associated instance.",
                    why=(
                        "Unattached EIPs cost ~$3.65/mo in idle fees. "
                        "Attach it or release it."
                    ),
                    monthly_impact_estimate="~$3.65/mo",
                )
            )
    return violations


def rule_load_balancer(resources: list[dict]) -> list[CostViolation]:
    return [
        CostViolation(
            rule_id="load_balancer",
            severity="WARN",
            message=f"Load balancer '{r['name']}' ({r['type']}) found.",
            why=(
                "ALBs and NLBs cost ~$16+/mo in base fees before any traffic. "
                "The golden path routes via nginx-ingress on k3s — no separate LB needed."
            ),
            monthly_impact_estimate="~$16+/mo",
        )
        for r in resources
        if r.get("type") in ("aws_lb", "aws_alb")
    ]


def rule_uncapped_rds_storage(resources: list[dict]) -> list[CostViolation]:
    violations = []
    for r in resources:
        if r.get("type") != "aws_db_instance":
            continue
        cfg = r.get("config", {})
        try:
            allocated = int(cfg.get("allocated_storage", 0))
        except (ValueError, TypeError):
            allocated = 0
        has_max = cfg.get("max_allocated_storage") is not None
        if allocated > 20 and not has_max:
            violations.append(
                CostViolation(
                    rule_id="uncapped_rds_storage",
                    severity="WARN",
                    message=(
                        f"RDS instance '{r['name']}' has allocated_storage={allocated}GB "
                        "with no max_allocated_storage cap."
                    ),
                    why=(
                        "Without max_allocated_storage, RDS autoscaling expands storage "
                        "unboundedly. Set a cap matching your expected data growth."
                    ),
                    monthly_impact_estimate="varies",
                )
            )
    return violations


def rule_multiplied_resource(resources: list[dict]) -> list[CostViolation]:
    violations = []
    for r in resources:
        if r.get("type") not in _STANDING_COST_TYPES:
            continue
        cfg = r.get("config", {})
        if cfg.get("count") or cfg.get("for_each"):
            multiplier = cfg.get("count") or "for_each"
            violations.append(
                CostViolation(
                    rule_id="multiplied_resource",
                    severity="WARN",
                    message=(
                        f"Billable resource '{r['name']}' ({r['type']}) "
                        f"uses count/for_each ({multiplier}) — verify the multiplier."
                    ),
                    why=(
                        "count and for_each multiply standing-cost resources. "
                        "An accidental count=10 on an EC2 instance means 10× the bill."
                    ),
                    monthly_impact_estimate="varies (multiplied)",
                )
            )
    return violations


def rule_no_pause_path(resources: list[dict]) -> list[CostViolation]:
    """Flag standing-cost resources when no pause/destroy path is discoverable."""
    standing = [r for r in resources if r.get("type") in _STANDING_COST_TYPES]
    if not standing:
        return []

    # Look for a Makefile with pause + destroy targets near the tf files
    candidate_dirs: set[Path] = set()
    for r in standing:
        tf_file = r.get("file", "")
        if tf_file:
            candidate_dirs.add(Path(tf_file).parent)
            candidate_dirs.add(Path(tf_file).parent.parent)

    for d in candidate_dirs:
        makefile = d / "Makefile"
        if makefile.exists():
            text = makefile.read_text()
            if "pause" in text and "destroy" in text:
                return []

    names = ", ".join(f"'{r['name']}'" for r in standing[:3])
    if len(standing) > 3:
        names += f" (+{len(standing) - 3} more)"

    return [
        CostViolation(
            rule_id="no_pause_path",
            severity="WARN",
            message=f"Standing-cost resource(s) {names} have no visible pause/destroy path.",
            why=(
                "Resources that run 24/7 accumulate cost even when idle. "
                "A Makefile with `pause` and `destroy` targets lets you stop paying "
                "when the infrastructure is not in use."
            ),
            monthly_impact_estimate=None,
        )
    ]


# ── Registry ──────────────────────────────────────────────────────────────────

_ALL_RULES = [
    rule_nat_gateway,
    rule_on_demand_instance,
    rule_oversized_instance,
    rule_unattached_eip,
    rule_load_balancer,
    rule_uncapped_rds_storage,
    rule_multiplied_resource,
    rule_no_pause_path,
]


def run_cost_rules(resources: list[dict]) -> list[CostViolation]:
    violations: list[CostViolation] = []
    for rule_fn in _ALL_RULES:
        violations.extend(rule_fn(resources))
    return violations
