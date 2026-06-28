"""Tests for engine/precheck.py — timeout, HTTP failure, and success paths."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

import deployguard.engine.precheck as _mod
from deployguard.config import AppConfig
from deployguard.engine import PreCheckResult, run_precheck
from deployguard.manifest import ServiceManifest


@pytest.fixture()
def manifest():
    return ServiceManifest(name="payments-api", image="payments-api:abc123", port=8000)


@pytest.fixture()
def config():
    return AppConfig()


@pytest.fixture(autouse=True)
def _patch_common(monkeypatch, tmp_path):
    """Patch helpers that every test needs: green manifests, sleep, delete."""
    monkeypatch.setattr(_mod, "_patch_green_manifests", lambda m, d: "---\n")
    monkeypatch.setattr(_mod, "_sleep", lambda _: None)
    monkeypatch.setattr(_mod, "_delete_green", lambda *_: None)
    monkeypatch.setattr(_mod, "_free_port", lambda: 19876)
    fake_pf = MagicMock()
    fake_pf.wait.return_value = None
    monkeypatch.setattr(_mod, "_open_port_forward", lambda cmd: fake_pf)


def _ok_kubectl(cmd, timeout=300, stdin=None):
    return subprocess.CompletedProcess(cmd, 0, "", "")


# ── Timeout ───────────────────────────────────────────────────────────────────

def test_precheck_timeout_on_rollout(monkeypatch, manifest, config):
    def kubectl(cmd, timeout=300, stdin=None):
        if "rollout" in cmd:
            raise subprocess.TimeoutExpired(cmd, timeout)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(_mod, "_run_kubectl", kubectl)

    result = run_precheck(manifest, config, k8s_dir=None)
    assert result.passed is False
    assert result.reason == "Readiness timeout"


def test_precheck_nonzero_rollout_treated_as_timeout(monkeypatch, manifest, config):
    def kubectl(cmd, timeout=300, stdin=None):
        rc = 1 if "rollout" in cmd else 0
        return subprocess.CompletedProcess(cmd, rc, "", "timed out")

    monkeypatch.setattr(_mod, "_run_kubectl", kubectl)

    result = run_precheck(manifest, config, k8s_dir=None)
    assert result.passed is False
    assert result.reason == "Readiness timeout"


# ── HTTP failure ──────────────────────────────────────────────────────────────

def test_precheck_http_non_200(monkeypatch, manifest, config):
    monkeypatch.setattr(_mod, "_run_kubectl", _ok_kubectl)

    fake_resp = MagicMock()
    fake_resp.status_code = 503
    monkeypatch.setattr(_mod, "_http_get", lambda url, timeout=5.0: fake_resp)

    result = run_precheck(manifest, config, k8s_dir=None)
    assert result.passed is False
    assert result.reason == "/readyz returned 503"


def test_precheck_http_unreachable(monkeypatch, manifest, config):
    monkeypatch.setattr(_mod, "_run_kubectl", _ok_kubectl)
    monkeypatch.setattr(
        _mod, "_http_get",
        lambda url, timeout=5.0: (_ for _ in ()).throw(
            __import__("requests").ConnectionError("refused")
        ),
    )

    result = run_precheck(manifest, config, k8s_dir=None)
    assert result.passed is False
    assert "/readyz unreachable" in result.reason


# ── Success ───────────────────────────────────────────────────────────────────

def test_precheck_success(monkeypatch, manifest, config):
    monkeypatch.setattr(_mod, "_run_kubectl", _ok_kubectl)

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    monkeypatch.setattr(_mod, "_http_get", lambda url, timeout=5.0: fake_resp)

    result = run_precheck(manifest, config, k8s_dir=None)
    assert result == PreCheckResult(passed=True, reason=None)
