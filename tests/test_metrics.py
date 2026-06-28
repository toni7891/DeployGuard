"""Tests for engine/metrics.py and the Prometheus-gated rollout checker."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest
import requests

import deployguard.engine.metrics as _metrics_mod
import deployguard.engine.rollout as _rollout_mod
from deployguard.config import AppConfig
from deployguard.engine import get_error_rate, make_prometheus_checker
from deployguard.manifest import ServiceManifest


def _prom_response(value: float) -> MagicMock:
    """Build a mock requests.Response matching the Prometheus /api/v1/query shape."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": {}, "value": [1_700_000_000.0, str(value)]}],
        },
    }
    return resp


# ── get_error_rate ─────────────────────────────────────────────────────────────

def test_get_error_rate_parses_prometheus_response(monkeypatch):
    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: _prom_response(2.5))
    rate = get_error_rate("payments-api", "default")
    assert rate == pytest.approx(2.5)


def test_get_error_rate_connection_error_returns_zero(monkeypatch):
    def _fail(url, params, timeout=5.0):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(_metrics_mod, "_http_get", _fail)
    rate = get_error_rate("payments-api", "default")
    assert rate == 0.0


def test_get_error_rate_timeout_returns_zero(monkeypatch):
    def _fail(url, params, timeout=5.0):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(_metrics_mod, "_http_get", _fail)
    rate = get_error_rate("payments-api", "default")
    assert rate == 0.0


def test_get_error_rate_empty_result_returns_zero(monkeypatch):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"status": "success", "data": {"resultType": "vector", "result": []}}
    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: resp)
    assert get_error_rate("payments-api", "default") == 0.0


def test_get_error_rate_malformed_json_returns_zero(monkeypatch):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"unexpected": "shape"}
    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: resp)
    assert get_error_rate("payments-api", "default") == 0.0


def test_get_error_rate_uses_window_in_query(monkeypatch):
    captured: list[dict] = []

    def _capture(url, params, timeout=5.0):
        captured.append(params)
        return _prom_response(0.0)

    monkeypatch.setattr(_metrics_mod, "_http_get", _capture)
    get_error_rate("svc", "default", window="5m")
    assert "5m" in captured[0]["query"]


# ── make_prometheus_checker ────────────────────────────────────────────────────

@pytest.fixture()
def manifest():
    return ServiceManifest(name="payments-api", image="payments-api:v1")


@pytest.fixture()
def config():
    return AppConfig()  # error_rate_threshold=1.0


def test_checker_passes_when_rate_below_threshold(monkeypatch, manifest, config):
    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: _prom_response(0.5))
    checker = make_prometheus_checker(manifest, config)
    assert checker(0, 10) is True


def test_checker_fails_when_rate_exceeds_threshold(monkeypatch, manifest, config):
    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: _prom_response(5.0))
    checker = make_prometheus_checker(manifest, config)
    assert checker(0, 10) is False


def test_checker_passes_when_prometheus_unreachable(monkeypatch, manifest, config):
    """Metrics down should never abort a deploy."""
    def _fail(url, params, timeout=5.0):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(_metrics_mod, "_http_get", _fail)
    checker = make_prometheus_checker(manifest, config)
    assert checker(0, 10) is True


# ── Integration: rollout returns False when error rate too high ────────────────

def test_rollout_aborts_when_checker_detects_high_error_rate(monkeypatch, manifest, config):
    # Patch rollout infra so no real kubectl is called
    monkeypatch.setattr(_rollout_mod, "_sleep", lambda _: None)
    monkeypatch.setattr(_rollout_mod, "_run_kubectl",
                        lambda cmd, timeout=120, stdin=None: subprocess.CompletedProcess(cmd, 0, "", ""))
    rolled_back: list[bool] = []
    monkeypatch.setattr(_rollout_mod, "rollback", lambda m: rolled_back.append(True))
    monkeypatch.setattr(_rollout_mod, "_delete_canary", lambda m: None)

    # High error rate from Prometheus
    monkeypatch.setattr(_metrics_mod, "_http_get",
                        lambda url, params, timeout=5.0: _prom_response(10.0))

    from deployguard.engine.rollout import rollout_traffic

    checker = make_prometheus_checker(manifest, config)
    result = rollout_traffic(manifest, config, on_step=checker)

    assert result is False
    assert rolled_back == [True]
