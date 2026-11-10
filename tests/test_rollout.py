"""Tests for engine/rollout.py — step iteration, rollback trigger, promotion."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

import deployguard.engine.rollout as _mod
from deployguard.config import AppConfig
from deployguard.engine import RolloutStep, rollback, rollout_traffic
from deployguard.manifest import ServiceManifest


@pytest.fixture()
def manifest():
    return ServiceManifest(name="payments-api", image="payments-api:abc123", port=8000)


@pytest.fixture()
def config():
    return AppConfig()  # default rollout_steps=[10, 50, 100]


def _ok(cmd, timeout=120, stdin=None):
    return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture(autouse=True)
def _patch_slow(monkeypatch):
    """Suppress sleep and kubectl side-effects for all tests."""
    monkeypatch.setattr(_mod, "_sleep", lambda _: None)
    monkeypatch.setattr(_mod, "_run_kubectl", _ok)


# ── Step iteration ─────────────────────────────────────────────────────────────

def test_all_steps_called_on_success(manifest, config):
    seen: list[tuple[int, int]] = []

    def on_step(idx, weight):
        seen.append((idx, weight))
        return True

    result = rollout_traffic(manifest, config, on_step=on_step)

    assert result is True
    assert seen == [(0, 10), (1, 50), (2, 100)]


def test_step_weights_match_config(manifest):
    cfg = AppConfig()
    cfg.deploy.rollout_steps  # [10, 50, 100]
    weights_seen: list[int] = []

    rollout_traffic(manifest, cfg, on_step=lambda i, w: weights_seen.append(w) or True)

    assert weights_seen == [10, 50, 100]


# ── Rollback on on_step False ──────────────────────────────────────────────────

def test_rollback_called_when_on_step_returns_false(monkeypatch, manifest, config):
    rolled_back: list[bool] = []
    monkeypatch.setattr(_mod, "rollback", lambda m: rolled_back.append(True))
    monkeypatch.setattr(_mod, "_delete_canary", lambda m: None)

    result = rollout_traffic(manifest, config, on_step=lambda i, w: False)

    assert result is False
    assert rolled_back == [True]


def test_rollback_called_on_second_step_failure(monkeypatch, manifest, config):
    rolled_back: list[bool] = []
    monkeypatch.setattr(_mod, "rollback", lambda m: rolled_back.append(True))
    monkeypatch.setattr(_mod, "_delete_canary", lambda m: None)

    calls: list[int] = []

    def on_step(i, w):
        calls.append(i)
        return i < 1  # pass step 0 (10%), fail step 1 (50%)

    result = rollout_traffic(manifest, config, on_step=on_step)

    assert result is False
    assert calls == [0, 1]
    assert rolled_back == [True]


# ── Promotion on success ───────────────────────────────────────────────────────

def test_promote_called_on_completion(monkeypatch, manifest, config):
    promoted: list[bool] = []
    deleted: list[bool] = []
    monkeypatch.setattr(_mod, "_promote", lambda m: promoted.append(True))
    monkeypatch.setattr(_mod, "_delete_canary", lambda m: deleted.append(True))

    result = rollout_traffic(manifest, config, on_step=lambda i, w: True)

    assert result is True
    assert promoted == [True]
    assert deleted == [True]


def test_promote_not_called_on_rollback(monkeypatch, manifest, config):
    promoted: list[bool] = []
    monkeypatch.setattr(_mod, "_promote", lambda m: promoted.append(True))
    monkeypatch.setattr(_mod, "rollback", lambda m: None)
    monkeypatch.setattr(_mod, "_delete_canary", lambda m: None)

    rollout_traffic(manifest, config, on_step=lambda i, w: False)

    assert promoted == []


# ── rollback() standalone ──────────────────────────────────────────────────────

def test_rollback_deletes_canary_and_undoes(monkeypatch, manifest):
    cmds: list[list[str]] = []

    def capture(cmd, timeout=120, stdin=None):
        cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(_mod, "_run_kubectl", capture)

    rollback(manifest)

    # Should delete deployment, service, ingress canary resources
    joined = [" ".join(c) for c in cmds]
    assert any("delete" in c and "canary" in c for c in joined)
    # The stable deployment was not touched during the canary steps: undoing it
    # would replace the healthy stable version with the one before it.
    assert not any("rollout" in c and "undo" in c for c in joined)

    cmds.clear()
    rollback(manifest, undo_stable=True)
    joined = [" ".join(c) for c in cmds]
    assert any("rollout" in c and "undo" in c for c in joined)


# ── RolloutStep model ─────────────────────────────────────────────────────────

def test_rollout_step_defaults():
    step = RolloutStep(weight=10)
    assert step.status == "pending"


def test_rollout_step_statuses():
    for status in ("pending", "active", "done", "failed"):
        s = RolloutStep(weight=50, status=status)
        assert s.status == status


# ── Canary resources: routing and parity with the stable workload ─────────────

def _stable_service_and_ingress(manifest):
    import yaml
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    env = Environment(
        loader=FileSystemLoader(str(Path(_mod.__file__).parent.parent / "templates")),
        undefined=StrictUndefined,
    )
    ctx = dict(name=manifest.name, namespace=manifest.namespace, port=manifest.port,
               host=manifest.host)
    svc = yaml.safe_load(env.get_template("k8s/service.yaml.j2").render(**ctx))
    ing = yaml.safe_load(env.get_template("k8s/ingress.yaml.j2").render(**ctx))
    return svc, ing


def test_canary_ingress_matches_stable_ingress(manifest):
    import yaml

    _, stable = _stable_service_and_ingress(manifest)
    canary = yaml.safe_load(_mod._canary_ingress_yaml(manifest, 10))

    assert canary["metadata"]["namespace"] == stable["metadata"]["namespace"] == "deployguard"
    assert canary["spec"]["ingressClassName"] == stable["spec"]["ingressClassName"] == "nginx"
    c_rule, s_rule = canary["spec"]["rules"][0], stable["spec"]["rules"][0]
    assert c_rule["host"] == s_rule["host"] == "payments-api.local"
    c_path, s_path = c_rule["http"]["paths"][0], s_rule["http"]["paths"][0]
    assert (c_path["path"], c_path["pathType"]) == (s_path["path"], s_path["pathType"])
    assert c_path["backend"]["service"] == {"name": "payments-api-canary", "port": {"number": 8000}}
    assert s_path["backend"]["service"] == {"name": "payments-api", "port": {"number": 8000}}
    ann = canary["metadata"]["annotations"]
    assert ann["nginx.ingress.kubernetes.io/canary"] == "true"
    assert ann["nginx.ingress.kubernetes.io/canary-weight"] == "10"


def test_patch_and_delete_target_the_canary_ingress(monkeypatch, manifest):
    import yaml

    cmds: list[list[str]] = []
    monkeypatch.setattr(_mod, "_run_kubectl",
                        lambda cmd, timeout=120, stdin=None: cmds.append(cmd) or _ok(cmd))
    name = yaml.safe_load(_mod._canary_ingress_yaml(manifest, 0))["metadata"]["name"]
    _mod._patch_ingress_weight(manifest, 50)
    _mod._delete_canary(manifest)
    assert cmds[0][:4] == ["kubectl", "annotate", "ingress", name]
    assert "nginx.ingress.kubernetes.io/canary-weight=50" in cmds[0]
    assert ["kubectl", "delete", "ingress", name, "-n", "deployguard", "--ignore-not-found"] in cmds


def test_canary_service_routes_only_canary_pods(manifest):
    import yaml

    stable_svc, _ = _stable_service_and_ingress(manifest)
    svc = yaml.safe_load(_mod._canary_service_yaml(manifest))
    dep = yaml.safe_load(_mod._canary_deployment_yaml(manifest))
    pod_labels = dep["spec"]["template"]["metadata"]["labels"]

    assert svc["metadata"]["namespace"] == dep["metadata"]["namespace"] == "deployguard"
    assert svc["spec"]["selector"] == {"app": "payments-api", "track": "canary"}
    assert dep["spec"]["selector"]["matchLabels"] == svc["spec"]["selector"]
    assert svc["spec"]["selector"].items() <= pod_labels.items()
    assert svc["spec"]["ports"][0]["port"] == svc["spec"]["ports"][0]["targetPort"] == 8000
    assert dep["spec"]["template"]["spec"]["containers"][0]["ports"][0]["containerPort"] == 8000
    # the stable Service must not select canary pods, or weights are meaningless
    assert not stable_svc["spec"]["selector"].items() <= pod_labels.items()


def test_canary_deployment_not_weaker_than_stable(manifest):
    import yaml

    stable = _mod._default_stable_deployment(manifest)
    canary = yaml.safe_load(_mod._canary_deployment_yaml(manifest, "local"))
    s_pod, c_pod = stable["spec"]["template"]["spec"], canary["spec"]["template"]["spec"]

    assert c_pod["securityContext"] == s_pod["securityContext"]
    assert c_pod["securityContext"]["runAsUser"] == 10001
    assert c_pod["serviceAccountName"] == s_pod["serviceAccountName"] == "payments-api"
    assert c_pod["volumes"] == s_pod["volumes"]
    s_c, c_c = s_pod["containers"][0], c_pod["containers"][0]
    for key in ("securityContext", "resources", "livenessProbe", "readinessProbe", "volumeMounts"):
        assert c_c[key] == s_c[key], key
    assert c_c["image"] == "payments-api:abc123"
    assert c_c["imagePullPolicy"] == "Never"


def test_canary_deployment_guard_clean(manifest, tmp_path):
    from deployguard.config import AppConfig
    from deployguard.guard import guard

    (tmp_path / "canary.yaml").write_text(_mod._canary_deployment_yaml(manifest))
    result = guard(str(tmp_path), AppConfig().guard.rules.as_dict(),
                   run_kubeconform=False, run_trivy=False)
    assert result.violations == []


def test_canary_pull_policy_for_aws_pulls_from_registry(manifest):
    import yaml

    dep = yaml.safe_load(_mod._canary_deployment_yaml(manifest, "aws"))
    assert dep["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] == "IfNotPresent"


def test_canary_cloned_from_project_deployment(manifest):
    """Customisations in k8s/deployment.yaml (env, etc.) carry over to the canary."""
    import yaml

    stable = _mod._default_stable_deployment(manifest)
    stable["spec"]["template"]["spec"]["containers"][0]["env"] = [{"name": "X", "value": "1"}]
    dep = yaml.safe_load(_mod._canary_deployment_yaml(manifest, "local", stable))
    assert dep["metadata"]["name"] == "payments-api-canary"
    assert dep["spec"]["template"]["spec"]["containers"][0]["env"] == [{"name": "X", "value": "1"}]
    assert dep["spec"]["template"]["metadata"]["labels"]["track"] == "canary"
    # the caller's stable doc is not mutated
    assert stable["metadata"]["name"] == "payments-api"


def test_rollout_passes_target_and_stable_spec_to_canary(monkeypatch, manifest, config):
    seen: list[str] = []
    monkeypatch.setattr(_mod, "_run_kubectl",
                        lambda cmd, timeout=120, stdin=None: (seen.append(stdin or ""), _ok(cmd))[1])
    stable = _mod._default_stable_deployment(manifest)
    rollout_traffic(manifest, config, on_step=lambda i, w: True, stable_deployment=stable)
    applied = next(s for s in seen if "kind: Deployment" in s)
    assert "imagePullPolicy: Never" in applied
    assert "runAsUser: 10001" in applied


def test_failed_promotion_undoes_stable(monkeypatch, manifest, config):
    cmds: list[str] = []

    def _run(cmd, timeout=120, stdin=None):
        cmds.append(" ".join(cmd))
        rc = 1 if cmd[:4] == ["kubectl", "rollout", "status", "deployment/payments-api"] else 0
        return subprocess.CompletedProcess(cmd, rc, "", "timed out")

    monkeypatch.setattr(_mod, "_run_kubectl", _run)
    with pytest.raises(RuntimeError, match="stable restored"):
        rollout_traffic(manifest, config, on_step=lambda i, w: True)
    assert any("rollout undo deployment/payments-api" in c for c in cmds)


def test_canary_not_ready_aborts_before_any_traffic(monkeypatch, manifest, config):
    cmds: list[str] = []

    def _run(cmd, timeout=120, stdin=None):
        cmds.append(" ".join(cmd))
        rc = 1 if "deployment/payments-api-canary" in cmd else 0
        return subprocess.CompletedProcess(cmd, rc, "", "progress deadline exceeded")

    monkeypatch.setattr(_mod, "_run_kubectl", _run)
    with pytest.raises(RuntimeError, match="Canary deployment did not become ready"):
        rollout_traffic(manifest, config, on_step=lambda i, w: True)
    assert not any("canary-weight=" in c for c in cmds)
    assert any("delete ingress payments-api-canary" in c for c in cmds)
    assert not any("rollout undo" in c for c in cmds)


# ── after_steps hook (stops test traffic before rollback / promotion) ─────────

def test_after_steps_runs_before_promotion(monkeypatch, manifest, config):
    events: list[str] = []
    monkeypatch.setattr(_mod, "_promote", lambda m: events.append("promote"))
    result = rollout_traffic(
        manifest, config, on_step=lambda i, w: True,
        after_steps=lambda: events.append("after_steps"),
    )
    assert result is True
    assert events == ["after_steps", "promote"]


def test_after_steps_runs_before_rollback(monkeypatch, manifest, config):
    events: list[str] = []
    monkeypatch.setattr(_mod, "rollback", lambda m: events.append("rollback"))
    result = rollout_traffic(
        manifest, config, on_step=lambda i, w: False,
        after_steps=lambda: events.append("after_steps"),
    )
    assert result is False
    assert events == ["after_steps", "rollback"]


def test_after_steps_runs_when_a_step_raises(monkeypatch, manifest, config):
    events: list[str] = []

    def _boom(m, w):
        raise RuntimeError("annotate failed")

    monkeypatch.setattr(_mod, "_patch_ingress_weight", _boom)
    with pytest.raises(RuntimeError):
        rollout_traffic(
            manifest, config, on_step=lambda i, w: True,
            after_steps=lambda: events.append("after_steps"),
        )
    assert events == ["after_steps"]
