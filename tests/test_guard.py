"""
Guard test suite.
All tests run without real kubeconform/trivy binaries (run_kubeconform=False,
run_trivy=False). Manifest dicts drive the policy rules directly.
"""
import pytest

from deployguard.guard import GuardResult, Severity, Violation, format_result, guard


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def default_rules() -> dict[str, str]:
    return {
        "require_resource_limits": "error",
        "require_probes": "error",
        "require_security_context": "error",
        "no_latest_tag": "error",
        "no_root_user": "error",
        "no_privileged_containers": "error",
        "iam_least_privilege": "warn",
    }


def _write_and_guard(tmp_path, yaml_content: str, rules: dict[str, str]) -> GuardResult:
    f = tmp_path / "manifest.yaml"
    f.write_text(yaml_content)
    return guard(str(f), rules, run_kubeconform=False, run_trivy=False)


# ── Base YAML snippets ────────────────────────────────────────────────────────

# Fully compliant Deployment — start here and remove/change one field per test.
_GOOD_DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  template:
    spec:
      securityContext:
        runAsNonRoot: true
      containers:
      - name: api
        image: myapp:1.2.3
        resources:
          requests:
            cpu: 100m
            memory: 128Mi
          limits:
            cpu: 500m
            memory: 256Mi
        livenessProbe:
          httpGet:
            path: /healthz
            port: 8000
        readinessProbe:
          httpGet:
            path: /readyz
            port: 8000
        securityContext:
          runAsUser: 1000
          allowPrivilegeEscalation: false
          privileged: false
"""


# ── Tests 1–6: individual rule violations ─────────────────────────────────────

def test_resource_limits_missing(tmp_path, default_rules):
    yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  template:
    spec:
      securityContext:
        runAsNonRoot: true
      containers:
      - name: api
        image: myapp:1.0.0
        livenessProbe:
          httpGet: {path: /healthz, port: 8000}
        readinessProbe:
          httpGet: {path: /readyz, port: 8000}
        securityContext:
          runAsUser: 1000
          privileged: false
"""
    result = _write_and_guard(tmp_path, yaml, default_rules)

    assert result.passed is False
    rl = [v for v in result.violations if v.rule_id == "require_resource_limits"]
    assert len(rl) == 1, f"Expected 1 require_resource_limits violation, got {len(rl)}"
    assert "starve the node" in rl[0].why
    assert "resources.requests" in rl[0].fix


def test_probes_missing(tmp_path, default_rules):
    yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  template:
    spec:
      securityContext:
        runAsNonRoot: true
      containers:
      - name: api
        image: myapp:1.0.0
        resources:
          requests: {cpu: 100m, memory: 128Mi}
          limits: {cpu: 500m, memory: 256Mi}
        securityContext:
          runAsUser: 1000
          privileged: false
"""
    result = _write_and_guard(tmp_path, yaml, default_rules)

    assert result.passed is False
    probes = [v for v in result.violations if v.rule_id == "require_probes"]
    assert len(probes) == 1
    assert "traffic to pods that aren't ready" in probes[0].why


def test_security_context_missing(tmp_path, default_rules):
    yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  template:
    spec:
      containers:
      - name: api
        image: myapp:1.0.0
        resources:
          requests: {cpu: 100m, memory: 128Mi}
          limits: {cpu: 500m, memory: 256Mi}
        livenessProbe:
          httpGet: {path: /healthz, port: 8000}
        readinessProbe:
          httpGet: {path: /readyz, port: 8000}
        securityContext:
          runAsUser: 1000
          privileged: false
"""
    result = _write_and_guard(tmp_path, yaml, default_rules)

    assert result.passed is False
    sc = [v for v in result.violations if v.rule_id == "require_security_context"]
    assert len(sc) == 1
    assert "host root" in sc[0].why


def test_latest_tag(tmp_path, default_rules):
    yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  template:
    spec:
      securityContext:
        runAsNonRoot: true
      containers:
      - name: api
        image: myapp:latest
        resources:
          requests: {cpu: 100m, memory: 128Mi}
          limits: {cpu: 500m, memory: 256Mi}
        livenessProbe:
          httpGet: {path: /healthz, port: 8000}
        readinessProbe:
          httpGet: {path: /readyz, port: 8000}
        securityContext:
          runAsUser: 1000
          privileged: false
"""
    result = _write_and_guard(tmp_path, yaml, default_rules)

    assert result.passed is False
    lt = [v for v in result.violations if v.rule_id == "no_latest_tag"]
    assert len(lt) == 1
    assert "mutable" in lt[0].why


def test_root_user(tmp_path, default_rules):
    yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  template:
    spec:
      securityContext:
        runAsNonRoot: true
      containers:
      - name: api
        image: myapp:1.0.0
        resources:
          requests: {cpu: 100m, memory: 128Mi}
          limits: {cpu: 500m, memory: 256Mi}
        livenessProbe:
          httpGet: {path: /healthz, port: 8000}
        readinessProbe:
          httpGet: {path: /readyz, port: 8000}
        securityContext:
          runAsUser: 0
          privileged: false
"""
    result = _write_and_guard(tmp_path, yaml, default_rules)

    assert result.passed is False
    ru = [v for v in result.violations if v.rule_id == "no_root_user"]
    assert len(ru) == 1
    assert "UID 0" in ru[0].why


def test_privileged_container(tmp_path, default_rules):
    yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  template:
    spec:
      securityContext:
        runAsNonRoot: true
      containers:
      - name: api
        image: myapp:1.0.0
        resources:
          requests: {cpu: 100m, memory: 128Mi}
          limits: {cpu: 500m, memory: 256Mi}
        livenessProbe:
          httpGet: {path: /healthz, port: 8000}
        readinessProbe:
          httpGet: {path: /readyz, port: 8000}
        securityContext:
          runAsUser: 1000
          privileged: true
"""
    result = _write_and_guard(tmp_path, yaml, default_rules)

    assert result.passed is False
    priv = [v for v in result.violations if v.rule_id == "no_privileged_containers"]
    assert len(priv) == 1
    assert "host kernel" in priv[0].why


# ── Test 7: fully compliant manifest ─────────────────────────────────────────

def test_passing_manifest(tmp_path, default_rules):
    result = _write_and_guard(tmp_path, _GOOD_DEPLOYMENT, default_rules)

    assert result.passed is True
    assert result.violations == []
    assert result.has_errors is False


# ── Test 8: warn vs error — iam_least_privilege ───────────────────────────────

def test_warn_vs_error(tmp_path, default_rules):
    # ServiceAccount without automountServiceAccountToken: false triggers iam rule.
    # With iam_least_privilege: "warn" it should be WARN, not ERROR → passed == True.
    yaml = """\
apiVersion: v1
kind: ServiceAccount
metadata:
  name: default
automountServiceAccountToken: true
"""
    result = _write_and_guard(tmp_path, yaml, default_rules)

    iam = [v for v in result.violations if v.rule_id == "iam_least_privilege"]
    assert len(iam) == 1, f"Expected 1 iam_least_privilege violation, got {len(iam)}"
    assert iam[0].severity == Severity.WARN
    # Warnings alone do not fail the guard
    assert result.passed is True
    assert result.has_errors is False


# ── Tests 9–10: format_result ────────────────────────────────────────────────

def test_format_result_with_explain(tmp_path, default_rules):
    # Use a bad manifest so we have violations to format.
    result = _write_and_guard(tmp_path, _GOOD_DEPLOYMENT.replace("myapp:1.2.3", "myapp:latest"), default_rules)

    output = format_result(result, explain=True)

    assert "Why it matters:" in output
    assert "What to add:" in output
    assert "guard" in output  # summary line present


def test_format_result_without_explain(tmp_path, default_rules):
    result = _write_and_guard(tmp_path, _GOOD_DEPLOYMENT.replace("myapp:1.2.3", "myapp:latest"), default_rules)

    output = format_result(result, explain=False)

    assert "Why it matters:" not in output
    assert "What to add:" not in output
    assert "guard" in output  # summary line still present


# ── Test 11: yaml_parse violation on malformed YAML ──────────────────────────

def test_yaml_parse_violation(tmp_path, default_rules):
    # Malformed YAML should produce a named yaml_parse ERROR, not a crash.
    broken = tmp_path / "broken.yaml"
    broken.write_text("key: [unclosed bracket\n")

    result = guard(str(broken), default_rules, run_kubeconform=False, run_trivy=False)

    assert result.passed is False
    parse_v = [v for v in result.violations if v.rule_id == "yaml_parse"]
    assert len(parse_v) == 1
    assert parse_v[0].severity == Severity.ERROR
    assert "fix" in parse_v[0].fix.lower() or "syntax" in parse_v[0].fix.lower()
