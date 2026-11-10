"""Verify that rendered golden-path templates pass guard with zero errors and zero warnings."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from deployguard.guard import Severity, guard
from deployguard.manifest import ServiceManifest

_TEMPLATES_DIR = Path(__file__).parent.parent / "deployguard" / "templates"

_CTX = {
    "name": "payments-api",
    "port": 8000,
    "replicas": 2,
    "namespace": ServiceManifest(name="payments-api").namespace,
    "health_liveness": "/healthz",
    "health_readiness": "/readyz",
    "host": ServiceManifest(name="payments-api").host,
}

_K8S_TEMPLATES = [
    "k8s/deployment.yaml.j2",
    "k8s/service.yaml.j2",
    "k8s/serviceaccount.yaml.j2",
    "k8s/ingress.yaml.j2",
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


def _assert_clean(result) -> None:
    assert result.violations == [], (
        "Golden-path templates must produce zero guard errors and zero warnings.\n"
        + "\n".join(
            f"  {v.severity.value} [{v.rule_id}] {v.message}" for v in result.violations
        )
    )


def test_templates_pass_guard_with_no_errors_or_warnings(rendered_k8s):
    result = guard(
        str(rendered_k8s),
        _DEFAULT_RULES,
        run_kubeconform=False,
        run_trivy=False,
    )
    _assert_clean(result)


@pytest.mark.skipif(
    not (shutil.which("trivy") and shutil.which("kubeconform")),
    reason="trivy and kubeconform required (same tools dg init runs)",
)
def test_templates_pass_full_guard_with_no_errors_or_warnings(rendered_k8s):
    """Same check dg init runs: policy rules + kubeconform + trivy."""
    result = guard(
        str(rendered_k8s),
        _DEFAULT_RULES,
        run_kubeconform=True,
        run_trivy=True,
    )
    _assert_clean(result)


def test_templates_use_non_default_namespace(rendered_k8s):
    for f in rendered_k8s.glob("*.yaml"):
        assert "namespace: deployguard" in f.read_text(), f.name
    assert _CTX["namespace"] == "deployguard"


def test_dockerfile_user_matches_pod_security_context():
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    dockerfile = env.get_template("Dockerfile.j2").render(**_CTX)
    deployment = env.get_template("k8s/deployment.yaml.j2").render(**_CTX)

    user = re.search(r"^USER (\d+):(\d+)$", dockerfile, re.MULTILINE)
    assert user, "Dockerfile must switch to a numeric non-root USER uid:gid"
    uid, gid = int(user.group(1)), int(user.group(2))
    assert uid > 10000 and gid > 10000
    assert f"runAsUser: {uid}" in deployment
    assert f"runAsGroup: {gid}" in deployment
    assert f"fsGroup: {gid}" in deployment
    assert _CTX["port"] > 1024, "non-root container cannot bind a privileged port"


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


def test_stable_ingress_routes_host_to_stable_service(rendered_k8s):
    import yaml

    ing = yaml.safe_load((rendered_k8s / "ingress.yaml").read_text())
    svc = yaml.safe_load((rendered_k8s / "service.yaml").read_text())
    assert ing["kind"] == "Ingress"
    assert ing["metadata"]["namespace"] == svc["metadata"]["namespace"] == "deployguard"
    assert ing["spec"]["ingressClassName"] == "nginx"
    assert "canary" not in str(ing.get("metadata", {}).get("annotations", {}))
    rule = ing["spec"]["rules"][0]
    assert rule["host"] == "payments-api.local"
    path = rule["http"]["paths"][0]
    assert (path["path"], path["pathType"]) == ("/", "Prefix")
    assert path["backend"]["service"]["name"] == svc["metadata"]["name"]
    assert path["backend"]["service"]["port"]["number"] == svc["spec"]["ports"][0]["port"]


def test_stable_service_selects_only_stable_pods(rendered_k8s):
    import yaml

    dep = yaml.safe_load((rendered_k8s / "deployment.yaml").read_text())
    svc = yaml.safe_load((rendered_k8s / "service.yaml").read_text())
    pod_labels = dep["spec"]["template"]["metadata"]["labels"]
    assert svc["spec"]["selector"] == {"app": "payments-api", "track": "stable"}
    assert dep["spec"]["selector"]["matchLabels"] == svc["spec"]["selector"]
    assert svc["spec"]["selector"].items() <= pod_labels.items()
    assert svc["spec"]["ports"][0]["targetPort"] == dep["spec"]["template"]["spec"]["containers"][0]["ports"][0]["containerPort"]


@pytest.mark.skipif(not shutil.which("kubeconform"), reason="kubeconform required")
def test_ingress_passes_kubeconform(rendered_k8s):
    import subprocess

    proc = subprocess.run(
        ["kubeconform", "-strict", "-summary", str(rendered_k8s / "ingress.yaml")],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
