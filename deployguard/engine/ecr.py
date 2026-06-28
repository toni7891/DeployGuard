"""ECR push helper: create repo if needed, authenticate, tag, and push."""
from __future__ import annotations

import base64
import shutil
import subprocess

import boto3


# ── Private helpers (monkeypatch-friendly) ─────────────────────────────────────

def _get_ecr_client(region: str):
    return boto3.client("ecr", region_name=region)


def _ensure_repo(client, repo_name: str) -> None:
    """Create ECR repo if it doesn't already exist."""
    try:
        client.describe_repositories(repositoryNames=[repo_name])
    except client.exceptions.RepositoryNotFoundException:
        client.create_repository(repositoryName=repo_name)


def _ecr_login(client, account_id: str, region: str) -> None:
    """Docker login to ECR using a token fetched via boto3."""
    token = client.get_authorization_token(registryIds=[account_id])
    raw = token["authorizationData"][0]["authorizationToken"]
    password = base64.b64decode(raw).decode("utf-8").split(":", 1)[1]
    registry = f"{account_id}.dkr.ecr.{region}.amazonaws.com"
    result = subprocess.run(
        ["docker", "login", "--username", "AWS", "--password-stdin", registry],
        input=password,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ECR docker login failed:\n{result.stderr}")


def _docker_tag(source: str, target: str) -> None:
    result = subprocess.run(
        ["docker", "tag", source, target],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker tag failed:\n{result.stderr}")


def _docker_push(image_uri: str) -> None:
    result = subprocess.run(
        ["docker", "push", image_uri],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker push failed:\n{result.stderr}")


# ── Public API ─────────────────────────────────────────────────────────────────

def push_to_ecr(image_name: str, tag: str, region: str, account_id: str) -> str:
    """Push image_name:tag to ECR, creating the repository if it doesn't exist.

    Returns the full ECR image URI.
    """
    if not shutil.which("docker"):
        raise RuntimeError(
            "docker not found. Install Docker Desktop and run `dg doctor` to verify."
        )

    registry = f"{account_id}.dkr.ecr.{region}.amazonaws.com"
    image_uri = f"{registry}/{image_name}:{tag}"

    client = _get_ecr_client(region)
    _ensure_repo(client, image_name)
    _ecr_login(client, account_id, region)
    _docker_tag(f"{image_name}:{tag}", image_uri)
    _docker_push(image_uri)

    return image_uri
