"""Tests for engine/ecr.py — push_to_ecr with mocked boto3 and docker subprocess."""
from __future__ import annotations

import base64
import subprocess
from unittest.mock import MagicMock

import pytest

import deployguard.engine.ecr as _mod
from deployguard.engine.ecr import push_to_ecr

_REGION = "us-east-1"
_ACCOUNT = "123456789012"
_IMAGE = "payments-api"
_TAG = "abc1234"
_REGISTRY = f"{_ACCOUNT}.dkr.ecr.{_REGION}.amazonaws.com"
_URI = f"{_REGISTRY}/{_IMAGE}:{_TAG}"

_ENCODED_TOKEN = base64.b64encode(b"AWS:supersecret").decode()


def _make_ecr_client(*, repo_exists: bool = True) -> MagicMock:
    client = MagicMock()
    if repo_exists:
        client.describe_repositories.return_value = {"repositories": []}
    else:
        client.exceptions.RepositoryNotFoundException = Exception
        client.describe_repositories.side_effect = Exception("not found")
        client.create_repository.return_value = {}
    client.get_authorization_token.return_value = {
        "authorizationData": [{"authorizationToken": _ENCODED_TOKEN}]
    }
    return client


def _ok_proc(*args, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args, 0, "", "")


def _fail_proc(stderr_msg: str):
    def _inner(*args, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args, 1, "", stderr_msg)
    return _inner


@pytest.fixture(autouse=True)
def _patch_ecr(monkeypatch):
    """Default: mocked boto3 client + successful docker subprocess calls."""
    monkeypatch.setattr(_mod, "_get_ecr_client", lambda region: _make_ecr_client())
    monkeypatch.setattr(subprocess, "run", _ok_proc)


# ── Happy path ─────────────────────────────────────────────────────────────────

def test_push_to_ecr_returns_uri():
    uri = push_to_ecr(_IMAGE, _TAG, _REGION, _ACCOUNT)
    assert uri == _URI


def test_push_to_ecr_calls_ensure_repo(monkeypatch):
    client = _make_ecr_client()
    monkeypatch.setattr(_mod, "_get_ecr_client", lambda region: client)
    push_to_ecr(_IMAGE, _TAG, _REGION, _ACCOUNT)
    client.describe_repositories.assert_called_once_with(repositoryNames=[_IMAGE])


def test_push_to_ecr_calls_docker_tag_and_push(monkeypatch):
    calls: list[list[str]] = []

    def capture_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", capture_run)

    push_to_ecr(_IMAGE, _TAG, _REGION, _ACCOUNT)

    tag_calls = [c for c in calls if c[:2] == ["docker", "tag"]]
    push_calls = [c for c in calls if c[:2] == ["docker", "push"]]
    assert len(tag_calls) == 1
    assert tag_calls[0][2] == f"{_IMAGE}:{_TAG}"
    assert tag_calls[0][3] == _URI
    assert len(push_calls) == 1
    assert push_calls[0][2] == _URI


def test_push_to_ecr_docker_login_uses_ecr_token(monkeypatch):
    login_inputs: list[str] = []

    def capture_run(cmd, **kwargs):
        if cmd[1:3] == ["login", "--username"]:
            login_inputs.append(kwargs.get("input", ""))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", capture_run)

    push_to_ecr(_IMAGE, _TAG, _REGION, _ACCOUNT)

    assert login_inputs == ["supersecret"]


# ── Repo creation ──────────────────────────────────────────────────────────────

def test_push_to_ecr_creates_repo_when_missing(monkeypatch):
    client = _make_ecr_client(repo_exists=False)
    monkeypatch.setattr(_mod, "_get_ecr_client", lambda region: client)
    push_to_ecr(_IMAGE, _TAG, _REGION, _ACCOUNT)
    client.create_repository.assert_called_once_with(repositoryName=_IMAGE)


def test_push_to_ecr_no_create_when_repo_exists(monkeypatch):
    client = _make_ecr_client(repo_exists=True)
    monkeypatch.setattr(_mod, "_get_ecr_client", lambda region: client)
    push_to_ecr(_IMAGE, _TAG, _REGION, _ACCOUNT)
    client.create_repository.assert_not_called()


# ── Failure cases ──────────────────────────────────────────────────────────────

def test_push_to_ecr_raises_on_docker_login_failure(monkeypatch):
    def bad_run(cmd, **kwargs):
        if cmd[1:3] == ["login", "--username"]:
            return subprocess.CompletedProcess(cmd, 1, "", "unauthorized")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", bad_run)

    with pytest.raises(RuntimeError, match="ECR docker login failed"):
        push_to_ecr(_IMAGE, _TAG, _REGION, _ACCOUNT)


def test_push_to_ecr_raises_on_docker_push_failure(monkeypatch):
    def bad_run(cmd, **kwargs):
        if cmd[1] == "push":
            return subprocess.CompletedProcess(cmd, 1, "", "push denied")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", bad_run)

    with pytest.raises(RuntimeError, match="docker push failed"):
        push_to_ecr(_IMAGE, _TAG, _REGION, _ACCOUNT)


def test_push_to_ecr_raises_on_docker_tag_failure(monkeypatch):
    def bad_run(cmd, **kwargs):
        if cmd[1] == "tag":
            return subprocess.CompletedProcess(cmd, 1, "", "no such image")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", bad_run)

    with pytest.raises(RuntimeError, match="docker tag failed"):
        push_to_ecr(_IMAGE, _TAG, _REGION, _ACCOUNT)


# ── deploy._push_image_ecr integration ────────────────────────────────────────

def test_deploy_push_image_ecr_calls_push_to_ecr(monkeypatch):
    """_push_image_ecr splits image_ref and delegates to push_to_ecr."""
    import importlib
    _deploy = importlib.import_module("deployguard.engine.deploy")

    pushed: list[tuple] = []
    monkeypatch.setattr(_deploy, "_aws_region", lambda: _REGION)
    monkeypatch.setattr(_deploy, "_aws_account_id", lambda region: _ACCOUNT)
    monkeypatch.setattr(_deploy, "push_to_ecr", lambda name, tag, region, account_id: pushed.append((name, tag, region, account_id)) or _URI)

    _deploy._push_image_ecr(f"{_IMAGE}:{_TAG}")

    assert pushed == [(_IMAGE, _TAG, _REGION, _ACCOUNT)]
