"""Tests for engine/deploy.py — full sequence, precheck failure, rollback, exception."""
from __future__ import annotations

import contextlib
import importlib
import subprocess

import pytest

import deployguard.engine.metrics as _metrics_mod
import deployguard.engine.precheck as _precheck_mod
import deployguard.engine.rollout as _rollout_mod
from deployguard.config import AppConfig
from deployguard.engine.deploy import deploy
from deployguard.engine.precheck import PreCheckResult
from deployguard.guard import GuardResult
from deployguard.manifest import ServiceManifest

# Import the module object directly — avoids the function/submodule name collision
# that occurs when deployguard.engine.__init__ re-exports a symbol named "deploy".
_mod = importlib.import_module("deployguard.engine.deploy")
_REAL_APPLY_STABLE = _mod._apply_stable  # captured before the autouse fixture patches it
_ORIGINAL_BUILD = _mod._build_image


@pytest.fixture()
def manifest():
    return ServiceManifest(name="payments-api", port=8000)


@pytest.fixture()
def config():
    return AppConfig()


def _ok_kubectl(cmd, timeout=120, stdin=None):
    return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture(autouse=True)
def _patch_infra(monkeypatch):
    """Suppress all real I/O for every test in this module."""
    monkeypatch.setattr(_mod, "_git_sha", lambda: "abc1234")
    monkeypatch.setattr(_mod, "_build_image", lambda name, tag: None)
    monkeypatch.setattr(_mod, "_load_image_local", lambda ref: None)
    monkeypatch.setattr(_mod, "_push_image_ecr", lambda ref: None)
    monkeypatch.setattr(_mod, "try_write_audit", lambda *a, **kw: None)
    # guard — passes by default
    monkeypatch.setattr(_mod, "guard", lambda path, rules, **kw: GuardResult(passed=True, violations=[]))
    monkeypatch.setattr(_mod, "format_result", lambda result, **kw: "")
    # stable deployment exists by default (tests canary path)
    monkeypatch.setattr(_mod, "_stable_deployment_exists", lambda name, ns: True)
    monkeypatch.setattr(_mod, "_apply_stable", lambda manifest, k8s_dir, target: None)
    # rollout infra
    monkeypatch.setattr(_rollout_mod, "_sleep", lambda _: None)
    monkeypatch.setattr(_rollout_mod, "_run_kubectl", _ok_kubectl)
    # metrics — 0% error rate (healthy)
    from unittest.mock import MagicMock
    ok_resp = MagicMock()
    ok_resp.raise_for_status.return_value = None
    ok_resp.json.return_value = {
        "status": "success",
        "data": {"resultType": "vector", "result": [{"metric": {}, "value": [0, "0.0"]}]},
    }
    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: ok_resp)
    # precheck — patched per-test
    monkeypatch.setattr(
        _precheck_mod, "_patch_green_manifests", lambda m, d: "---\n"
    )
    monkeypatch.setattr(_precheck_mod, "_sleep", lambda _: None)
    monkeypatch.setattr(_precheck_mod, "_delete_green", lambda *_: None)
    monkeypatch.setattr(_precheck_mod, "_free_port", lambda: 19876)
    from unittest.mock import MagicMock as MM
    fake_pf = MM()
    fake_pf.wait.return_value = None
    monkeypatch.setattr(_precheck_mod, "_open_port_forward", lambda cmd: fake_pf)
    # managed port-forwards / test traffic — never touch a real cluster
    monkeypatch.setattr(_mod, "prometheus_endpoint", _fake_prometheus_endpoint)
    monkeypatch.setattr(_mod, "smoke_traffic", _fake_smoke_traffic(None))


@contextlib.contextmanager
def _fake_prometheus_endpoint(config):
    yield "http://localhost:9090"


def _fake_smoke_traffic(gen):
    @contextlib.contextmanager
    def _cm(manifest, config):
        yield gen
    return _cm


# ── Success path ───────────────────────────────────────────────────────────────

def test_deploy_success_returns_true(monkeypatch, manifest, config):
    # precheck passes
    monkeypatch.setattr(_precheck_mod, "_run_kubectl", _ok_kubectl)
    from unittest.mock import MagicMock
    ok_http = MagicMock()
    ok_http.status_code = 200
    monkeypatch.setattr(_precheck_mod, "_http_get", lambda url, timeout=5.0: ok_http)

    result = deploy(manifest, config)
    assert result is True


def test_deploy_sets_image_tag_on_manifest(monkeypatch, manifest, config):
    """Image ref passed to build matches git SHA."""
    built: list[str] = []
    monkeypatch.setattr(_mod, "_build_image", lambda name, tag: built.append(tag))
    monkeypatch.setattr(_precheck_mod, "_run_kubectl", _ok_kubectl)
    from unittest.mock import MagicMock
    ok_http = MagicMock()
    ok_http.status_code = 200
    monkeypatch.setattr(_precheck_mod, "_http_get", lambda url, timeout=5.0: ok_http)

    deploy(manifest, config)
    assert built == ["abc1234"]


# ── First deploy (no stable deployment) ───────────────────────────────────────

def test_deploy_first_deploy_calls_apply_stable(monkeypatch, manifest, config):
    monkeypatch.setattr(_mod, "_stable_deployment_exists", lambda name, ns: False)
    apply_called: list[bool] = []
    monkeypatch.setattr(_mod, "_apply_stable", lambda m, k8s, t: apply_called.append(True))
    rollout_called: list[bool] = []
    monkeypatch.setattr(_mod, "rollout_traffic", lambda *a, **kw: rollout_called.append(True) or True)
    monkeypatch.setattr(_precheck_mod, "_run_kubectl", _ok_kubectl)
    from unittest.mock import MagicMock
    ok_http = MagicMock()
    ok_http.status_code = 200
    monkeypatch.setattr(_precheck_mod, "_http_get", lambda url, timeout=5.0: ok_http)

    result = deploy(manifest, config)
    assert result is True
    assert apply_called == [True]
    assert rollout_called == [], "canary rollout must not run on first deploy"


# ── Guard failure ─────────────────────────────────────────────────────────────

def test_deploy_returns_false_when_guard_fails(monkeypatch, manifest, config):
    from deployguard.guard import Severity, Violation
    bad_result = GuardResult(passed=False, violations=[
        Violation(
            rule_id="no_latest_tag",
            severity=Severity.ERROR,
            message="image uses :latest",
            why="mutable tag",
            fix="pin the tag",
        )
    ])
    monkeypatch.setattr(_mod, "guard", lambda path, rules, **kw: bad_result)
    build_called: list[bool] = []
    monkeypatch.setattr(_mod, "_build_image", lambda name, tag: build_called.append(True))

    result = deploy(manifest, config)
    assert result is False
    assert build_called == [], "build must not run when guard fails"


# ── Pre-check failure ──────────────────────────────────────────────────────────

def test_deploy_returns_false_when_precheck_fails(monkeypatch, manifest, config):
    monkeypatch.setattr(
        _mod, "run_precheck",
        lambda m, c, **kw: PreCheckResult(passed=False, reason="Readiness timeout"),
    )

    result = deploy(manifest, config)
    assert result is False


def test_deploy_no_rollout_when_precheck_fails(monkeypatch, manifest, config):
    monkeypatch.setattr(
        _mod, "run_precheck",
        lambda m, c, **kw: PreCheckResult(passed=False, reason="Readiness timeout"),
    )
    rollout_called: list[bool] = []
    monkeypatch.setattr(_mod, "rollout_traffic", lambda *a, **kw: rollout_called.append(True) or True)

    deploy(manifest, config)
    assert rollout_called == []


# ── Rollback on error-rate spike ───────────────────────────────────────────────

def test_deploy_returns_false_and_prints_rollback_on_error_rate_spike(
    monkeypatch, manifest, config
):
    # precheck passes
    monkeypatch.setattr(
        _mod, "run_precheck",
        lambda m, c, **kw: PreCheckResult(passed=True),
    )
    # high error rate — rollout_traffic returns False
    from unittest.mock import MagicMock
    high_err = MagicMock()
    high_err.raise_for_status.return_value = None
    high_err.json.return_value = {
        "status": "success",
        "data": {"resultType": "vector", "result": [{"metric": {}, "value": [0, "10.0"]}]},
    }
    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: high_err)
    rolled_back: list[bool] = []
    monkeypatch.setattr(_rollout_mod, "rollback", lambda m: rolled_back.append(True))
    monkeypatch.setattr(_rollout_mod, "_delete_canary", lambda m: None)

    result = deploy(manifest, config)
    assert result is False
    assert rolled_back == [True]


# ── Exception triggers rollback ────────────────────────────────────────────────

def test_deploy_exception_calls_rollback_and_reraises(monkeypatch, manifest, config):
    monkeypatch.setattr(
        _mod, "run_precheck",
        lambda m, c, **kw: PreCheckResult(passed=True),
    )
    monkeypatch.setattr(
        _mod, "rollout_traffic",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("cluster exploded")),
    )
    rollback_called: list[bool] = []
    monkeypatch.setattr(_mod, "rollback", lambda m: rollback_called.append(True))

    with pytest.raises(RuntimeError, match="cluster exploded"):
        deploy(manifest, config)

    assert rollback_called == [True]


# ── AWS target stub ───────────────────────────────────────────────────────────

def test_deploy_aws_calls_push_not_load(monkeypatch, manifest):
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"deploy": cfg.deploy.model_copy(update={"target": "aws"})})

    push_called: list[bool] = []
    load_called: list[bool] = []
    monkeypatch.setattr(_mod, "_push_image_ecr", lambda ref: push_called.append(True))
    monkeypatch.setattr(_mod, "_load_image_local", lambda ref: load_called.append(True))
    monkeypatch.setattr(
        _mod, "run_precheck",
        lambda m, c, **kw: PreCheckResult(passed=True),
    )
    from unittest.mock import MagicMock
    ok_http = MagicMock()
    ok_http.status_code = 200
    monkeypatch.setattr(_precheck_mod, "_http_get", lambda url, timeout=5.0: ok_http)
    monkeypatch.setattr(_precheck_mod, "_run_kubectl", _ok_kubectl)

    deploy(manifest, cfg)
    assert push_called == [True]
    assert load_called == []


# ── First deploy applies every scaffolded k8s/ file, including the Ingress ────

def test_first_deploy_applies_scaffolded_ingress(monkeypatch, tmp_path, config):
    import yaml

    from deployguard.scaffold import init as _init

    monkeypatch.setattr(_init, "get_adapter", lambda c: None)
    _init.scaffold_service("payments-api", str(tmp_path), config, no_guard=True)
    k8s_dir = tmp_path / "payments-api" / "k8s"

    applied: list[str] = []

    def _run(cmd, input=None, **kw):
        if input:
            applied.append(input)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(_mod.subprocess, "run", _run)
    m = ServiceManifest(name="payments-api", image="payments-api:abc1234")
    _REAL_APPLY_STABLE(m, str(k8s_dir), "local")

    docs = list(yaml.safe_load_all(applied[0]))
    kinds = sorted(d["kind"] for d in docs)
    assert kinds == ["Deployment", "Ingress", "Service", "ServiceAccount"]
    dep = next(d for d in docs if d["kind"] == "Deployment")
    assert dep["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] == "Never"

    stable = _mod._stable_deployment_doc(str(k8s_dir), "payments-api")
    assert stable["spec"]["template"]["metadata"]["labels"]["track"] == "stable"


# ── Managed Prometheus endpoint + test traffic on the canary path ─────────────

def test_canary_path_uses_managed_prometheus_and_stops_traffic(monkeypatch, manifest, config):
    monkeypatch.setattr(_mod, "run_precheck", lambda m, c, **kw: PreCheckResult(passed=True))

    @contextlib.contextmanager
    def _endpoint(cfg):
        yield "http://localhost:23456"

    class _Gen:
        stopped = 0

        def stop(self):
            self.stopped += 1

    gen = _Gen()
    monkeypatch.setattr(_mod, "prometheus_endpoint", _endpoint)
    monkeypatch.setattr(_mod, "smoke_traffic", _fake_smoke_traffic(gen))
    checker_kw: dict = {}
    monkeypatch.setattr(
        _mod, "make_prometheus_checker",
        lambda m, c, **kw: checker_kw.update(kw) or (lambda i, w: True),
    )
    rollout_kw: dict = {}

    def _rollout(m, c, on_step, **kw):
        rollout_kw.update(kw)
        kw["after_steps"]()
        return True

    monkeypatch.setattr(_mod, "rollout_traffic", _rollout)

    assert deploy(manifest, config) is True
    assert checker_kw["prometheus_url"] == "http://localhost:23456"
    assert checker_kw["wait_for_data"] > 0
    assert gen.stopped == 1


def test_canary_path_without_traffic_does_not_wait(monkeypatch, manifest, config):
    monkeypatch.setattr(_mod, "run_precheck", lambda m, c, **kw: PreCheckResult(passed=True))
    checker_kw: dict = {}
    monkeypatch.setattr(
        _mod, "make_prometheus_checker",
        lambda m, c, **kw: checker_kw.update(kw) or (lambda i, w: True),
    )
    rollout_kw: dict = {}
    monkeypatch.setattr(
        _mod, "rollout_traffic",
        lambda m, c, on_step, **kw: rollout_kw.update(kw) or True,
    )
    assert deploy(manifest, config) is True
    assert checker_kw["wait_for_data"] == 0.0
    assert rollout_kw["after_steps"] is None


# ── Quiet docker build ─────────────────────────────────────────────────────────

def test_build_prints_single_line_on_success(monkeypatch, capsys):
    monkeypatch.setattr(_mod.shutil, "which", lambda name: "/usr/bin/" + name)
    noisy = "\n".join(f"#{i} buildkit noise docker-desktop://x" for i in range(30))
    monkeypatch.setattr(
        _mod, "_run_docker_build",
        lambda ref: subprocess.CompletedProcess(["docker"], 0, noisy, None),
    )
    _ORIGINAL_BUILD("payments-api", "abc1234")
    out = capsys.readouterr().out
    assert "✓ Built payments-api:abc1234" in out
    assert "buildkit noise" not in out
    assert "docker-desktop://" not in out


def test_build_failure_shows_log_tail_and_raises(monkeypatch, capsys):
    monkeypatch.setattr(_mod.shutil, "which", lambda name: "/usr/bin/" + name)
    log = "\n".join(f"line {i}" for i in range(100)) + "\nERROR: pip install failed"
    monkeypatch.setattr(
        _mod, "_run_docker_build",
        lambda ref: subprocess.CompletedProcess(["docker"], 1, log, None),
    )
    with pytest.raises(RuntimeError, match=r"docker build failed \(exit 1\)") as exc:
        _ORIGINAL_BUILD("payments-api", "abc1234")
    assert "ERROR: pip install failed" in str(exc.value)
    assert "line 10\n" not in str(exc.value), "only the tail is kept"
    out = capsys.readouterr().out
    assert "ERROR: pip install failed" in out
    assert "line 70" in out
    assert "line 50\n" not in out
