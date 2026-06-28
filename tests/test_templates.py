"""Verify that rendered golden-path templates pass guard with zero errors."""
from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from deployguard.guard import Severity, guard

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

_CTX = {
    "name": "payments-api",
    "port": 8000,
    "replicas": 2,
    "namespace": "default",
    "health_liveness": "/healthz",
    "health_readiness": "/readyz",
}

_K8S_TEMPLATES = [
    "k8s/deployment.yaml.j2",
    "k8s/serviceaccount.yaml.j2",
]

_DEFAULT_RULES = {
    "require_resource_limits": "error",
    "require_probes": "error",
    "require_security_context": "error",
    "no_latest_tag": "error",
    "no_root_user": "error",
    "no_privileged_containers": "error",
    "iam_least_privilege": "warn",
}


@pytest.fixture(scope="module")
def rendered_k8s(tmp_path_factory):
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    out_dir = tmp_path_factory.mktemp("k8s")
    for tmpl_path in _K8S_TEMPLATES:
        content = env.get_template(tmpl_path).render(**_CTX)
        out_file = out_dir / Path(tmpl_path).name.replace(".j2", "")
        out_file.write_text(content)
    return out_dir


def test_templates_pass_guard_with_no_errors(rendered_k8s):
    result = guard(
        str(rendered_k8s),
        _DEFAULT_RULES,
        run_kubeconform=False,
        run_trivy=False,
    )
    errors = [v for v in result.violations if v.severity == Severity.ERROR]
    assert errors == [], (
        "Golden-path templates must produce zero guard errors.\n"
        + "\n".join(f"  [{v.rule_id}] {v.message}" for v in errors)
    )


def test_templates_no_latest_tag(rendered_k8s):
    result = guard(
        str(rendered_k8s),
        _DEFAULT_RULES,
        run_kubeconform=False,
        run_trivy=False,
    )
    latest = [v for v in result.violations if v.rule_id == "no_latest_tag"]
    assert latest == [], "Deployment image must not use :latest tag"


def test_templates_require_security_context(rendered_k8s):
    result = guard(
        str(rendered_k8s),
        _DEFAULT_RULES,
        run_kubeconform=False,
        run_trivy=False,
    )
    sc = [v for v in result.violations if v.rule_id == "require_security_context"]
    assert sc == [], "Deployment must have runAsNonRoot: true"


def test_templates_require_probes(rendered_k8s):
    result = guard(
        str(rendered_k8s),
        _DEFAULT_RULES,
        run_kubeconform=False,
        run_trivy=False,
    )
    probes = [v for v in result.violations if v.rule_id == "require_probes"]
    assert probes == [], "Deployment must have liveness and readiness probes"


def test_templates_serviceaccount_no_automount(rendered_k8s):
    result = guard(
        str(rendered_k8s),
        _DEFAULT_RULES,
        run_kubeconform=False,
        run_trivy=False,
    )
    iam = [v for v in result.violations if v.rule_id == "iam_least_privilege"]
    assert iam == [], "ServiceAccount must set automountServiceAccountToken: false"
