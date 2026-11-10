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


def test_get_error_rate_connection_error_returns_no_data(monkeypatch):
    def _fail(url, params, timeout=5.0):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(_metrics_mod, "_http_get", _fail)
    rate = get_error_rate("payments-api", "default")
    assert rate is None


def test_get_error_rate_timeout_returns_no_data(monkeypatch):
    def _fail(url, params, timeout=5.0):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(_metrics_mod, "_http_get", _fail)
    rate = get_error_rate("payments-api", "default")
    assert rate is None


def test_get_error_rate_empty_result_returns_no_data(monkeypatch):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"status": "success", "data": {"resultType": "vector", "result": []}}
    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: resp)
    assert get_error_rate("payments-api", "default") is None


def test_get_error_rate_malformed_json_returns_no_data(monkeypatch):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"unexpected": "shape"}
    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: resp)
    assert get_error_rate("payments-api", "default") is None


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


# ── Metrics gate visibility ────────────────────────────────────────────────────

def _empty_response() -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"status": "success", "data": {"resultType": "vector", "result": []}}
    return resp


def _refused(url, params, timeout=5.0):
    raise requests.ConnectionError("refused")


def test_check_metrics_available_statuses(monkeypatch):
    from deployguard.engine import MetricsStatus, check_metrics_available

    monkeypatch.setattr(_metrics_mod, "_http_get", _refused)
    assert check_metrics_available("svc", "deployguard") == MetricsStatus.UNREACHABLE

    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: _empty_response())
    assert check_metrics_available("svc", "deployguard") == MetricsStatus.NO_DATA

    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: _prom_response(3.0))
    assert check_metrics_available("svc", "deployguard") == MetricsStatus.OK


def test_get_error_rate_emits_no_python_warning(monkeypatch, recwarn):
    monkeypatch.setattr(_metrics_mod, "_http_get", _refused)
    assert get_error_rate("payments-api", "deployguard") is None
    assert len(recwarn) == 0


def test_unreachable_prometheus_prints_one_line_before_steps(monkeypatch, manifest, config, capsys, recwarn):
    monkeypatch.setattr(_rollout_mod, "_sleep", lambda _: None)
    monkeypatch.setattr(_rollout_mod, "_run_kubectl",
                        lambda cmd, timeout=120, stdin=None: subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(_metrics_mod, "_http_get", _refused)

    from deployguard.engine.rollout import rollout_traffic

    checker = make_prometheus_checker(manifest, config)
    assert rollout_traffic(manifest, config, on_step=checker) is True

    out = capsys.readouterr().out
    notice = (
        "Metrics gate inactive: Prometheus not reachable at http://localhost:9090. "
        "Rollout steps will not be gated on error rate."
    )
    assert out.count("Metrics gate inactive") == 1
    assert notice in out
    assert out.index(notice) < out.index("Rolling out")
    assert len(recwarn) == 0


def test_no_data_prints_distinct_line(monkeypatch, manifest, config, capsys):
    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: _empty_response())
    checker = make_prometheus_checker(manifest, config)
    assert checker(0, 10) is True
    out = capsys.readouterr().out
    assert "has no ingress request data for 'payments-api'" in out
    assert "not reachable" not in out


def test_reachable_with_data_prints_nothing(monkeypatch, manifest, config, capsys):
    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: _prom_response(0.1))
    make_prometheus_checker(manifest, config)
    assert "Metrics gate" not in capsys.readouterr().out


# ── Canary-scoped gate ─────────────────────────────────────────────────────────

def test_error_rate_promql_targets_canary_backend_in_namespace():
    from deployguard.engine.metrics import canary_error_rate_promql

    q = canary_error_rate_promql("payments-api", "deployguard")
    assert 'service="payments-api-canary"' in q
    assert 'canary=~"deployguard-payments-api-canary-.+"' in q
    assert 'namespace="deployguard"' in q
    assert "[30s]" in q
    # never the anchored regex that only matches the stable Service
    assert 'service=~"payments-api"' not in q
    assert 'service="payments-api"' not in q
    # zero traffic -> empty result ("no data"), not a division by zero
    assert "> 0)" in q
    assert "or vector(0)" in q


def test_get_error_rate_nan_is_no_data(monkeypatch):
    resp = _prom_response(0.0)
    resp.json.return_value["data"]["result"][0]["value"][1] = "NaN"
    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: resp)
    assert get_error_rate("payments-api", "deployguard") is None


def _fake_prometheus(canary_error_pct: float | None, queries: list[str] | None = None):
    """Fake /api/v1/query: the availability probe sees series, the gate query
    returns the canary 5xx ratio (or an empty vector for "no data")."""
    def _get(url, params, timeout=5.0):
        q = params["query"]
        if queries is not None:
            queries.append(q)
        if q.startswith("count("):
            return _prom_response(4.0)
        if canary_error_pct is None:
            return _empty_response()
        return _prom_response(canary_error_pct)
    return _get


def _no_kubectl(cmds: list[list[str]]):
    def _run(cmd, timeout=120, stdin=None):
        cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return _run


def test_healthy_canary_passes_every_step_and_promotes(monkeypatch, manifest, config):
    from deployguard.engine.rollout import rollout_traffic

    cmds: list[list[str]] = []
    queries: list[str] = []
    monkeypatch.setattr(_rollout_mod, "_sleep", lambda _: None)
    monkeypatch.setattr(_rollout_mod, "_run_kubectl", _no_kubectl(cmds))
    monkeypatch.setattr(_metrics_mod, "_http_get", _fake_prometheus(0.2, queries))

    checker = make_prometheus_checker(manifest, config)
    assert rollout_traffic(manifest, config, on_step=checker) is True

    gate_queries = [q for q in queries if not q.startswith("count(")]
    assert len(gate_queries) == len(config.deploy.rollout_steps)
    assert all("payments-api-canary" in q for q in gate_queries)
    joined = [" ".join(c) for c in cmds]
    assert any("set image deployment/payments-api" in c for c in joined)
    assert not any("rollout undo" in c for c in joined)


def test_canary_5xx_above_threshold_fails_step_and_rolls_back(monkeypatch, manifest, config):
    from deployguard.engine.rollout import rollout_traffic

    cmds: list[list[str]] = []
    monkeypatch.setattr(_rollout_mod, "_sleep", lambda _: None)
    monkeypatch.setattr(_rollout_mod, "_run_kubectl", _no_kubectl(cmds))
    monkeypatch.setattr(_metrics_mod, "_http_get", _fake_prometheus(25.0))

    checker = make_prometheus_checker(manifest, config)
    assert rollout_traffic(manifest, config, on_step=checker) is False

    joined = [" ".join(c) for c in cmds]
    # stopped at the first step: one weight patch, never promoted
    assert sum("canary-weight=" in c for c in joined) == 1
    assert not any("set image" in c for c in joined)
    # canary Deployment, Service and Ingress deleted; stable Ingress/Service kept
    for kind in ("deployment", "service", "ingress"):
        assert f"kubectl delete {kind} payments-api-canary -n deployguard --ignore-not-found" in joined
    deletes = [c for c in joined if c.startswith("kubectl delete")]
    assert len(deletes) == 3 and all("payments-api-canary" in c for c in deletes)
    # stable was never modified during the canary steps, so it is not undone
    assert not any("rollout undo" in c for c in joined)


def test_canary_without_traffic_is_no_data_and_gate_inactive(monkeypatch, manifest, config, capsys):
    from deployguard.engine.rollout import rollout_traffic

    monkeypatch.setattr(_rollout_mod, "_sleep", lambda _: None)
    monkeypatch.setattr(_rollout_mod, "_run_kubectl", _no_kubectl([]))
    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: _empty_response())

    assert get_error_rate("payments-api", "deployguard") is None
    checker = make_prometheus_checker(manifest, config)
    assert rollout_traffic(manifest, config, on_step=checker) is True
    out = capsys.readouterr().out
    assert out.count("Metrics gate inactive") == 1


def test_prometheus_url_comes_from_config(monkeypatch, manifest, capsys):
    from deployguard.config import load_config

    urls: list[str] = []

    def _get(url, params, timeout=5.0):
        urls.append(url)
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(_metrics_mod, "_http_get", _get)
    cfg = load_config(cli_overrides={"deploy": {"prometheus_url": "http://prom.example:9091"}})
    checker = make_prometheus_checker(manifest, cfg)
    assert checker(0, 10) is True
    assert urls and all(u.startswith("http://prom.example:9091/api/v1/query") for u in urls)
    assert "Prometheus not reachable at http://prom.example:9091" in capsys.readouterr().out


# ── Warm-up: wait for the first test requests to reach Prometheus ─────────────

def test_wait_for_data_polls_until_data_appears(monkeypatch, manifest, config, capsys):
    responses = [_empty_response(), _empty_response(), _prom_response(0.0)]
    calls: list[str] = []

    def _get(url, params, timeout=5.0):
        calls.append(params["query"])
        return responses.pop(0) if responses else _prom_response(0.0)

    monkeypatch.setattr(_metrics_mod, "_http_get", _get)
    slept: list[float] = []
    monkeypatch.setattr(_rollout_mod, "_sleep", lambda s: slept.append(s))
    make_prometheus_checker(manifest, config, wait_for_data=20.0)
    assert len(calls) == 3
    assert len(slept) == 2
    assert "Metrics gate" not in capsys.readouterr().out


def test_wait_for_data_gives_up_with_one_line(monkeypatch, manifest, config, capsys):
    monkeypatch.setattr(_metrics_mod, "_http_get", lambda url, params, timeout=5.0: _empty_response())
    monkeypatch.setattr(_rollout_mod, "_sleep", lambda s: None)
    make_prometheus_checker(manifest, config, wait_for_data=3.0)
    assert capsys.readouterr().out.count("Metrics gate inactive") == 1


def test_wait_for_data_does_not_wait_when_unreachable(monkeypatch, manifest, config):
    def _fail(url, params, timeout=5.0):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(_metrics_mod, "_http_get", _fail)
    slept: list[float] = []
    monkeypatch.setattr(_rollout_mod, "_sleep", lambda s: slept.append(s))
    make_prometheus_checker(manifest, config, wait_for_data=20.0)
    assert slept == []
