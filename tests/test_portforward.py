"""Tests for engine/portforward.py — managed kubectl port-forward lifecycle."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import deployguard.engine.portforward as _mod
from deployguard.config import AppConfig


def _fake_proc(exited: bool = False) -> MagicMock:
    proc = MagicMock()
    proc.poll.return_value = 1 if exited else None
    proc.wait.return_value = 0
    return proc


@pytest.fixture()
def spawned(monkeypatch):
    """Record kubectl commands; port 19999; no real sleeping."""
    cmds: list[list[str]] = []
    proc = _fake_proc()

    def _open(cmd):
        cmds.append(cmd)
        return proc

    monkeypatch.setattr(_mod, "_free_port", lambda: 19999)
    monkeypatch.setattr(_mod, "_open_port_forward", _open)
    monkeypatch.setattr(_mod, "_sleep", lambda _: None)
    monkeypatch.setattr(_mod, "_port_open", lambda port: True)
    return cmds, proc


def test_port_forward_yields_port_and_terminates(spawned):
    cmds, proc = spawned
    with _mod.port_forward("monitoring", "svc/prometheus", 9090) as port:
        assert port == 19999
        proc.terminate.assert_not_called()
    assert cmds == [["kubectl", "port-forward", "-n", "monitoring", "svc/prometheus", "19999:9090"]]
    proc.terminate.assert_called_once()


def test_port_forward_terminates_on_exception(spawned):
    _, proc = spawned
    with pytest.raises(RuntimeError):
        with _mod.port_forward("monitoring", "svc/prometheus", 9090):
            raise RuntimeError("rollout failed")
    proc.terminate.assert_called_once()


def test_port_forward_yields_none_when_kubectl_exits(spawned, monkeypatch):
    _, proc = spawned
    proc.poll.return_value = 1  # e.g. Service not found
    monkeypatch.setattr(_mod, "_port_open", lambda port: False)
    with _mod.port_forward("monitoring", "svc/prometheus", 9090) as port:
        assert port is None


def test_port_forward_yields_none_on_timeout(spawned, monkeypatch):
    _, proc = spawned
    monkeypatch.setattr(_mod, "_port_open", lambda port: False)
    with _mod.port_forward("monitoring", "svc/prometheus", 9090, timeout=1.0) as port:
        assert port is None
    proc.terminate.assert_called_once()


def test_port_forward_yields_none_when_kubectl_missing(monkeypatch):
    monkeypatch.setattr(_mod, "_free_port", lambda: 19999)

    def _missing(cmd):
        raise FileNotFoundError("kubectl")

    monkeypatch.setattr(_mod, "_open_port_forward", _missing)
    with _mod.port_forward("monitoring", "svc/prometheus", 9090) as port:
        assert port is None


def test_prometheus_endpoint_local_uses_managed_port_forward(spawned):
    cmds, proc = spawned
    with _mod.prometheus_endpoint(AppConfig()) as url:
        assert url == "http://localhost:19999"
    assert cmds[0][:5] == ["kubectl", "port-forward", "-n", "monitoring", "svc/prometheus"]
    proc.terminate.assert_called_once()


def test_prometheus_endpoint_explicit_url_starts_nothing(spawned):
    cmds, _ = spawned
    cfg = AppConfig(**{"deploy": {"prometheus_url": "http://prom:9091"}})
    with _mod.prometheus_endpoint(cfg) as url:
        assert url == "http://prom:9091"
    assert cmds == []


def test_prometheus_endpoint_aws_starts_nothing(spawned):
    cmds, _ = spawned
    cfg = AppConfig(**{"deploy": {"target": "aws"}})
    with _mod.prometheus_endpoint(cfg) as url:
        assert url == "http://localhost:9090"
    assert cmds == []


def test_prometheus_endpoint_falls_back_when_port_forward_fails(spawned, monkeypatch):
    _, proc = spawned
    proc.poll.return_value = 1
    monkeypatch.setattr(_mod, "_port_open", lambda port: False)
    with _mod.prometheus_endpoint(AppConfig()) as url:
        assert url == "http://localhost:9090"
