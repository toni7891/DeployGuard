"""Tests for provision/local.py — mocked minikube/kubectl; ingress metrics + Prometheus."""
from __future__ import annotations

import importlib.resources
import json
import subprocess

import pytest
import yaml

import deployguard.provision.local as _mod
from deployguard.config import AppConfig


def _controller_json(args: list[str]) -> str:
    return json.dumps({
        "spec": {"template": {"spec": {"containers": [
            {"name": "controller", "args": args},
        ]}}},
    })


class _FakeRun:
    """Dispatches subprocess.run by command; records every call."""

    def __init__(self, controller_args: list[str] | None):
        self.calls: list[tuple[list[str], str | None]] = []
        self.controller_args = controller_args

    def __call__(self, cmd, capture_output=True, text=True, timeout=None, input=None):
        self.calls.append((cmd, input))
        out = ""
        rc = 0
        if cmd[:2] == ["minikube", "status"]:
            out = json.dumps({"Host": "Running"})
        elif cmd[:3] == ["minikube", "addons", "list"]:
            out = json.dumps({"ingress": {"Status": "enabled"}})
        elif cmd[:3] == ["kubectl", "create", "namespace"]:
            out = "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: deployguard\n"
        elif cmd[:4] == ["kubectl", "get", "deployment", "ingress-nginx-controller"]:
            if self.controller_args is None:
                rc = 1
            else:
                out = _controller_json(self.controller_args)
        return subprocess.CompletedProcess(cmd, rc, out, "")

    def cmds(self) -> list[list[str]]:
        return [c for c, _ in self.calls]


@pytest.fixture()
def fake_run(monkeypatch):
    def _make(controller_args):
        fake = _FakeRun(controller_args)
        monkeypatch.setattr(_mod.subprocess, "run", fake)
        monkeypatch.setattr(_mod.shutil, "which", lambda name: "/usr/bin/" + name)
        return fake
    return _make


def test_enables_ingress_metrics_when_missing(fake_run, capsys):
    fake = fake_run(["/nginx-ingress-controller", "--election-id=ingress-nginx-leader"])
    _mod.provision_local(AppConfig())

    patches = [c for c in fake.cmds() if c[:2] == ["kubectl", "patch"]]
    assert len(patches) == 1
    patch = patches[0]
    assert patch[2:6] == ["deployment", "ingress-nginx-controller", "-n", "ingress-nginx"]
    ops = json.loads(patch[patch.index("-p") + 1])
    assert ops == [{
        "op": "add",
        "path": "/spec/template/spec/containers/0/args/-",
        "value": "--enable-metrics=true",
    }]
    assert [
        "kubectl", "rollout", "status", "deployment/ingress-nginx-controller",
        "-n", "ingress-nginx", "--timeout=180s",
    ] in fake.cmds()
    assert "✓ Enabling ingress metrics" in capsys.readouterr().out


def test_ingress_metrics_patch_is_idempotent(fake_run, capsys):
    fake = fake_run(["/nginx-ingress-controller", "--enable-metrics=true"])
    _mod.provision_local(AppConfig())
    assert not [c for c in fake.cmds() if c[:2] == ["kubectl", "patch"]]
    assert "✓ Enabling ingress metrics (already enabled)" in capsys.readouterr().out


def test_missing_ingress_controller_fails_clearly(fake_run):
    fake_run(None)
    with pytest.raises(RuntimeError, match="ingress addon"):
        _mod.provision_local(AppConfig())


def test_installs_prometheus_and_waits(fake_run, capsys):
    fake = fake_run(["--enable-metrics=true"])
    _mod.provision_local(AppConfig())

    applied = [stdin for cmd, stdin in fake.calls
               if cmd == ["kubectl", "apply", "-f", "-"] and stdin and "prometheus" in stdin]
    assert len(applied) == 1
    kinds = [d["kind"] for d in yaml.safe_load_all(applied[0]) if d]
    assert kinds == [
        "Namespace", "ServiceAccount", "ClusterRole", "ClusterRoleBinding",
        "ConfigMap", "Deployment", "Service",
    ]
    assert [
        "kubectl", "rollout", "status", "deployment/prometheus",
        "-n", "monitoring", "--timeout=180s",
    ] in fake.cmds()
    # Order: namespace first, then metrics, then Prometheus.
    cmds = fake.cmds()
    patch_or_get = next(i for i, c in enumerate(cmds) if c[:2] == ["kubectl", "get"])
    prom_apply = next(i for i, (c, s) in enumerate(fake.calls) if s and "prometheus" in s)
    assert patch_or_get < prom_apply
    out = capsys.readouterr().out
    assert "✓ Installing Prometheus (monitoring)" in out
    assert "✓ Waiting for Prometheus to be ready" in out


# ── Bundled manifest (package data) ───────────────────────────────────────────

def test_prometheus_manifest_is_package_data():
    res = importlib.resources.files("deployguard.provision") / "prometheus.yaml"
    assert res.is_file()
    assert _mod.PROMETHEUS_MANIFEST.read_text() == res.read_text()


def test_prometheus_manifest_contents():
    docs = {d["kind"]: d for d in yaml.safe_load_all(_mod.PROMETHEUS_MANIFEST.read_text()) if d}
    assert docs["Namespace"]["metadata"]["name"] == "monitoring"

    rules = docs["ClusterRole"]["rules"][0]
    assert set(rules["resources"]) == {"pods", "nodes", "services", "endpoints"}
    assert set(rules["verbs"]) == {"get", "list", "watch"}

    container = docs["Deployment"]["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "prom/prometheus:v2.55.1"
    assert "--storage.tsdb.retention.time=1h" in container["args"]
    assert "--config.file=/etc/prometheus/prometheus.yml" in container["args"]
    assert docs["Deployment"]["spec"]["template"]["spec"]["serviceAccountName"] == "prometheus"

    svc = docs["Service"]
    assert svc["metadata"]["namespace"] == "monitoring"
    assert svc["spec"]["type"] == "ClusterIP"
    assert svc["spec"]["ports"][0]["port"] == 9090

    prom = yaml.safe_load(docs["ConfigMap"]["data"]["prometheus.yml"])
    assert prom["global"]["scrape_interval"] == "5s"
    job = prom["scrape_configs"][0]
    assert job["job_name"] == "ingress-nginx"
    assert job["kubernetes_sd_configs"] == [
        {"role": "pod", "namespaces": {"names": ["ingress-nginx"]}}
    ]
    relabel = job["relabel_configs"]
    assert relabel[-1]["target_label"] == "__address__"
    assert relabel[-1]["replacement"] == "$1:10254"
