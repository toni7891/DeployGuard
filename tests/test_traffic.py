"""Tests for engine/traffic.py — local-only test traffic during the canary steps."""
from __future__ import annotations

import contextlib
import time

import deployguard.engine.traffic as _mod
from deployguard.config import AppConfig
from deployguard.manifest import ServiceManifest


def _manifest() -> ServiceManifest:
    return ServiceManifest(name="payments-api", port=8000)


def _fake_port_forward(port, calls):
    @contextlib.contextmanager
    def _pf(namespace, resource, remote_port, timeout=15.0):
        calls.append((namespace, resource, remote_port))
        yield port
    return _pf


def _wait_for(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_traffic_sends_requests_with_host_header(monkeypatch, capsys):
    pf_calls: list = []
    sent: list = []
    monkeypatch.setattr(_mod, "port_forward", _fake_port_forward(18080, pf_calls))
    monkeypatch.setattr(
        _mod, "_send_request",
        lambda port, host, path="/", timeout=2.0: sent.append((port, host, path)) or 200,
    )
    with _mod.smoke_traffic(_manifest(), AppConfig()) as gen:
        assert gen is not None
        assert _wait_for(lambda: len(sent) >= 2)
    assert not gen.running, "traffic thread must be stopped on exit"
    assert pf_calls == [("ingress-nginx", "svc/ingress-nginx-controller", 80)]
    assert sent[0] == (18080, "payments-api.local", "/")
    out = capsys.readouterr().out
    assert "traffic: sending test requests to payments-api.local (local target)" in out


def test_traffic_failures_are_counted_not_raised(monkeypatch, capsys):
    monkeypatch.setattr(_mod, "port_forward", _fake_port_forward(18080, []))

    def _boom(port, host, path="/", timeout=2.0):
        raise ConnectionRefusedError("nope")

    monkeypatch.setattr(_mod, "_send_request", _boom)
    with _mod.smoke_traffic(_manifest(), AppConfig()) as gen:
        assert _wait_for(lambda: gen.failed >= 2)
    assert gen.sent == 0
    out = capsys.readouterr().out
    assert "nope" not in out and "Error" not in out


def test_traffic_stop_is_idempotent(monkeypatch):
    monkeypatch.setattr(_mod, "port_forward", _fake_port_forward(18080, []))
    monkeypatch.setattr(_mod, "_send_request", lambda *a, **kw: 200)
    with _mod.smoke_traffic(_manifest(), AppConfig()) as gen:
        gen.stop()
        assert not gen.running
    assert not gen.running


def test_no_traffic_on_aws(monkeypatch):
    pf_calls: list = []
    monkeypatch.setattr(_mod, "port_forward", _fake_port_forward(18080, pf_calls))
    cfg = AppConfig(**{"deploy": {"target": "aws"}})
    with _mod.smoke_traffic(_manifest(), cfg) as gen:
        assert gen is None
    assert pf_calls == []


def test_no_traffic_when_disabled(monkeypatch):
    pf_calls: list = []
    monkeypatch.setattr(_mod, "port_forward", _fake_port_forward(18080, pf_calls))
    cfg = AppConfig(**{"deploy": {"smoke_traffic": False}})
    with _mod.smoke_traffic(_manifest(), cfg) as gen:
        assert gen is None
    assert pf_calls == []


def test_no_traffic_when_ingress_port_forward_fails(monkeypatch, capsys):
    monkeypatch.setattr(_mod, "port_forward", _fake_port_forward(None, []))
    with _mod.smoke_traffic(_manifest(), AppConfig()) as gen:
        assert gen is None
    assert "traffic:" not in capsys.readouterr().out
