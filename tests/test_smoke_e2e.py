"""Spine integration test — must always pass after every change.

Unit tests (no external deps): always run.
Integration tests: require k3d + docker, run with -m integration.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from deployguard.manifest import load_manifest

_HAS_K3D = shutil.which("k3d") is not None
_HAS_DOCKER = shutil.which("docker") is not None

_SERVICE_NAME = "test-svc"
_EXPECTED_FILES = [
    "Dockerfile",
    "app/main.py",
    "k8s/deployment.yaml",
    "k8s/service.yaml",
    "k8s/serviceaccount.yaml",
    "deployguard.yaml",
]


class TestInitUnit:
    """dg init unit tests — no cluster required, always run in CI."""

    def test_scaffold_creates_expected_files(self, tmp_path: Path) -> None:
        result = subprocess.run(
            ["dg", "init", _SERVICE_NAME],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"dg init exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
        )

        service_dir = tmp_path / _SERVICE_NAME
        for rel in _EXPECTED_FILES:
            assert (service_dir / rel).exists(), f"Missing expected file: {rel}"

    def test_deployguard_yaml_is_valid(self, tmp_path: Path) -> None:
        subprocess.run(
            ["dg", "init", _SERVICE_NAME],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        manifest = load_manifest(str(tmp_path / _SERVICE_NAME / "deployguard.yaml"))
        assert manifest.name == _SERVICE_NAME
        assert manifest.port == 8000


@pytest.mark.integration
@pytest.mark.skipif(
    not (_HAS_K3D and _HAS_DOCKER),
    reason="k3d and docker required for integration tests",
)
class TestE2EIntegration:
    """Full spine: init → provision → deploy → assert pods running.

    Run with: pytest tests/test_smoke_e2e.py -m integration
    Requires: k3d, docker, helm, kubectl.
    """

    def test_provision_and_deploy(self, tmp_path: Path) -> None:
        # 1. scaffold
        subprocess.run(
            ["dg", "init", _SERVICE_NAME],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        service_dir = tmp_path / _SERVICE_NAME

        # 2. provision (idempotent — safe if cluster already exists)
        result = subprocess.run(
            ["dg", "provision"],
            cwd=str(service_dir),
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert result.returncode == 0, (
            f"dg provision exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
        )

        # 3. deploy
        result = subprocess.run(
            ["dg", "deploy"],
            cwd=str(service_dir),
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, (
            f"dg deploy exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
        )

        # 4. assert at least one pod is Running
        pods_result = subprocess.run(
            [
                "kubectl", "get", "pods",
                "-n", "default",
                "-l", f"app={_SERVICE_NAME}",
                "-o", "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert pods_result.returncode == 0, "kubectl get pods failed"
        pods = json.loads(pods_result.stdout)
        running = [
            p for p in pods.get("items", [])
            if p.get("status", {}).get("phase") == "Running"
        ]
        assert running, f"No Running pods found for {_SERVICE_NAME} in namespace default"
