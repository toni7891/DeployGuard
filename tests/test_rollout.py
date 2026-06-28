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
    # Should also undo the stable deployment
    assert any("rollout" in c and "undo" in c for c in joined)


# ── RolloutStep model ─────────────────────────────────────────────────────────

def test_rollout_step_defaults():
    step = RolloutStep(weight=10)
    assert step.status == "pending"


def test_rollout_step_statuses():
    for status in ("pending", "active", "done", "failed"):
        s = RolloutStep(weight=50, status=status)
        assert s.status == status
