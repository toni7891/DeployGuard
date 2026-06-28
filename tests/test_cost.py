"""Tests for cost rules, reporter, and infracost wrapper."""
from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from deployguard.cost.infracost import run_infracost
from deployguard.cost.parser import parse_terraform_resources
from deployguard.cost.reporter import format_cost_report
from deployguard.cost.rules import CostViolation, rule_nat_gateway, rule_on_demand_instance, run_cost_rules

_INFRA_DIR = Path(__file__).parent.parent / "infra"


def test_nat_gateway_rejected():
    resources = [{"type": "aws_nat_gateway", "name": "main", "config": {}, "file": "infra/main.tf"}]
    violations = rule_nat_gateway(resources)
    assert len(violations) == 1
    v = violations[0]
    assert v.severity == "REJECT"
    assert "32" in v.why


def test_on_demand_warn():
    resources = [{"type": "aws_instance", "name": "worker", "config": {"instance_type": "t3.small"}, "file": "infra/main.tf"}]
    violations = rule_on_demand_instance(resources)
    assert len(violations) == 1
    assert violations[0].severity == "WARN"


def test_no_violations_clean_infra(tmp_path):
    (tmp_path / "Makefile").write_text("pause:\n\t@echo pause\ndestroy:\n\t@echo destroy\n")
    tf_file = str(tmp_path / "main.tf")
    resources = [
        {
            "type": "aws_instance",
            "name": "worker",
            "config": {"instance_type": "t3.small", "spot_price": "0.01"},
            "file": tf_file,
        }
    ]
    violations = run_cost_rules(resources)
    assert violations == []


def test_cost_report_format():
    report = format_cost_report([], [], {}, [])
    buf = StringIO()
    console = Console(file=buf, highlight=False, no_color=True, width=120)
    console.print(report)
    output = buf.getvalue()
    assert "What will spin up" in output
    assert "Estimated monthly cost" in output
    assert "Bill-inflation risks" in output
    assert "estimate" in output.lower()


def test_infracost_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(RuntimeError, match="infracost not found"):
        run_infracost("/some/dir")


# ── Real infra accuracy tests ──────────────────────────────────────────────────
# Validate that infra/main.tf matches the PRD §11 cost model:
#   Running: ~$6.25/mo (EC2 t3.small Spot + EBS 20GB + EIP + Route53)
#   Paused:  ~$3.55/mo (EIP idle + Route53; EC2/EBS absent via instance_count=0)
# Infracost is not required — rules run on the parsed resource list.

@pytest.fixture()
def real_infra_resources():
    return parse_terraform_resources(str(_INFRA_DIR))


def test_real_infra_has_no_nat_gateway(real_infra_resources):
    """No NAT Gateway must be present — adds ~$32/mo and violates the cost target."""
    violations = rule_nat_gateway(real_infra_resources)
    assert violations == [], (
        f"NAT Gateway found in infra/main.tf: {[v.message for v in violations]}"
    )


def test_real_infra_has_no_reject_violations(real_infra_resources):
    """No cost rule should hard-reject the golden-path infra."""
    rejects = [v for v in run_cost_rules(real_infra_resources) if v.severity == "REJECT"]
    assert rejects == [], f"Unexpected REJECT violations: {[v.message for v in rejects]}"


def test_real_infra_spot_detected(real_infra_resources):
    """EC2 instance must use Spot pricing — on-demand breaks the ~$6/mo target."""
    violations = rule_on_demand_instance(real_infra_resources)
    assert violations == [], (
        "EC2 instance in infra/main.tf appears to be on-demand; "
        "Spot market_type must be present in instance_market_options."
    )


def test_real_infra_pause_path_exists():
    """Makefile must have `pause` and `destroy` targets to satisfy rule_no_pause_path."""
    makefile = _INFRA_DIR.parent / "Makefile"
    assert makefile.exists(), "Makefile not found at project root"
    text = makefile.read_text()
    assert "pause" in text, "Makefile has no 'pause' target"
    assert "destroy" in text, "Makefile has no 'destroy' target"


def test_real_infra_pause_reduces_cost():
    """make pause sets instance_count=0 — confirm the Makefile encodes this."""
    makefile = (_INFRA_DIR.parent / "Makefile").read_text()
    assert "instance_count=0" in makefile, (
        "Makefile pause target must pass -var='instance_count=0' to drop EC2 cost"
    )
