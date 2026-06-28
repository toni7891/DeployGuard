"""Tests for provision/aws.py — mocked terraform, SSH, and cluster setup."""
from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest

import deployguard.provision.aws as _mod
from deployguard.config import AppConfig

# Capture real implementations before autouse fixtures patch them
_real_fetch_kubeconfig = _mod._fetch_kubeconfig
_real_get_terraform_output = _mod._get_terraform_output

_FAKE_EIP = "54.1.2.3"

_FAKE_KUBECONFIG = f"""\
apiVersion: v1
clusters:
- cluster:
    server: https://127.0.0.1:6443
  name: default
contexts:
- context:
    cluster: default
    user: default
  name: default
current-context: default
kind: Config
users:
- name: default
  user:
    token: fake-token
"""


@pytest.fixture()
def config():
    return AppConfig()


@pytest.fixture(autouse=True)
def _quiet_console(monkeypatch):
    """Suppress Rich output in tests."""
    from rich.console import Console
    monkeypatch.setattr(_mod, "console", Console(quiet=True))


@pytest.fixture()
def infra_dir(tmp_path):
    """Fake infra dir so existence check passes."""
    d = tmp_path / "infra"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _patch_all(monkeypatch, infra_dir):
    """Default: all external calls are no-ops returning success."""
    monkeypatch.setattr(
        _mod, "_run",
        lambda cmd, label, cwd=None, timeout=600, stdin=None: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    monkeypatch.setattr(_mod, "_get_terraform_output", lambda name, d=None: _FAKE_EIP)
    monkeypatch.setattr(_mod, "_wait_for_ssh", lambda host, timeout=300: None)
    monkeypatch.setattr(_mod, "_fetch_kubeconfig", lambda host, eip, ssh_key=None: _FAKE_KUBECONFIG)
    monkeypatch.setattr(_mod, "_merge_kubeconfig", lambda content, ctx: None)
    monkeypatch.setattr(_mod, "_setup_cluster", lambda: None)


# ── Happy path ────────────────────────────────────────────────────────────────

def test_provision_aws_succeeds(config, infra_dir):
    _mod.provision_aws(config, infra_dir=infra_dir)


def test_provision_aws_terraform_init_and_apply_called(monkeypatch, config, infra_dir):
    calls: list[list[str]] = []

    def capture(cmd, label, cwd=None, timeout=600, stdin=None):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(_mod, "_run", capture)

    _mod.provision_aws(config, infra_dir=infra_dir)

    tf_cmds = [" ".join(c) for c in calls if "terraform" in c]
    assert any("init" in c for c in tf_cmds)
    assert any("apply" in c for c in tf_cmds)


def test_provision_aws_eip_used_in_kubeconfig_fetch(monkeypatch, config, infra_dir):
    fetched: list[str] = []
    monkeypatch.setattr(
        _mod, "_fetch_kubeconfig",
        lambda host, eip, ssh_key=None: fetched.append(host) or _FAKE_KUBECONFIG,
    )
    _mod.provision_aws(config, infra_dir=infra_dir)
    assert fetched == [_FAKE_EIP]


def test_provision_aws_merges_kubeconfig(monkeypatch, config, infra_dir):
    merged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        _mod, "_merge_kubeconfig",
        lambda content, ctx: merged.append((content, ctx)),
    )
    _mod.provision_aws(config, infra_dir=infra_dir)
    assert len(merged) == 1
    assert merged[0][1] == "deployguard-aws"


def test_provision_aws_sets_up_cluster(monkeypatch, config, infra_dir):
    setup_called: list[bool] = []
    monkeypatch.setattr(_mod, "_setup_cluster", lambda: setup_called.append(True))
    _mod.provision_aws(config, infra_dir=infra_dir)
    assert setup_called == [True]


# ── Failure cases ─────────────────────────────────────────────────────────────

def test_provision_aws_raises_when_infra_dir_missing(config, tmp_path):
    with pytest.raises(RuntimeError, match="Terraform directory"):
        _mod.provision_aws(config, infra_dir=tmp_path / "nonexistent")


def test_provision_aws_raises_when_terraform_fails(monkeypatch, config, infra_dir):
    def fail_on_apply(cmd, label, cwd=None, timeout=600, stdin=None):
        if "apply" in cmd:
            raise RuntimeError("terraform apply failed (exit 1):\nError creating instance")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(_mod, "_run", fail_on_apply)

    with pytest.raises(RuntimeError, match="terraform apply failed"):
        _mod.provision_aws(config, infra_dir=infra_dir)


def test_provision_aws_raises_when_ssh_timeout(monkeypatch, config, infra_dir):
    monkeypatch.setattr(
        _mod, "_wait_for_ssh",
        lambda host, timeout=300: (_ for _ in ()).throw(RuntimeError("SSH on 54.1.2.3:22 not available after 300s")),
    )
    with pytest.raises(RuntimeError, match="SSH on"):
        _mod.provision_aws(config, infra_dir=infra_dir)


# ── _get_terraform_output ─────────────────────────────────────────────────────

def test_get_terraform_output_parses_json(monkeypatch, tmp_path):
    fake_dir = tmp_path / "infra2"
    fake_dir.mkdir()

    def fake_run(cmd, capture_output, text, cwd, timeout):
        return subprocess.CompletedProcess(cmd, 0, '"1.2.3.4"\n', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _real_get_terraform_output("eip_address", fake_dir)
    assert result == "1.2.3.4"


# ── _fetch_kubeconfig ──────────────────────────────────────────────────────────

def test_fetch_kubeconfig_replaces_loopback_with_eip(monkeypatch):
    def fake_ssh(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, 0, _FAKE_KUBECONFIG, "")

    monkeypatch.setattr(subprocess, "run", fake_ssh)
    result = _real_fetch_kubeconfig("54.1.2.3", "54.1.2.3")
    assert "127.0.0.1" not in result
    assert "54.1.2.3" in result
