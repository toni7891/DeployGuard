"""Parse Terraform .tf files and k8s YAML to extract resource inventory."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

_RESOURCE_RE = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', re.MULTILINE)
_KV_STR_RE = re.compile(r'^\s*(\w+)\s*=\s*"([^"]*)"')
_KV_BARE_RE = re.compile(r'^\s*(\w+)\s*=\s*([^\s"\{#][^\s#]*)')


def _extract_block(content: str, start: int) -> str:
    depth = 1
    i = start
    while i < len(content) and depth > 0:
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
        i += 1
    return content[start : i - 1]


def _extract_config(block: str) -> dict:
    config: dict = {}
    for line in block.splitlines():
        m = _KV_STR_RE.match(line)
        if m:
            config[m.group(1)] = m.group(2)
            continue
        m = _KV_BARE_RE.match(line)
        if m:
            val = m.group(2).strip()
            if val not in ("{", ""):
                config[m.group(1)] = val
    # Detect for_each (value may be a complex expression)
    if re.search(r'^\s*for_each\s*=', block, re.MULTILINE):
        config["for_each"] = True
    # Detect nested spot market block
    if re.search(r'market_type\s*=\s*"spot"', block):
        config["market_type"] = "spot"
    return config


def parse_terraform_resources(tf_dir: str) -> list[dict]:
    """Parse .tf files in tf_dir; returns resource dicts with no state required."""
    tf_path = Path(tf_dir)
    if not tf_path.is_dir():
        return []

    resources: list[dict] = []
    for tf_file in sorted(tf_path.glob("*.tf")):
        content = tf_file.read_text()
        for m in _RESOURCE_RE.finditer(content):
            block = _extract_block(content, m.end())
            resources.append(
                {
                    "type": m.group(1),
                    "name": m.group(2),
                    "config": _extract_config(block),
                    "file": str(tf_file),
                }
            )

    return resources


def parse_k8s_resources(k8s_dir: str) -> list[dict]:
    """Parse k8s YAML files; returns simplified resource dicts."""
    k8s_path = Path(k8s_dir)
    if not k8s_path.is_dir():
        return []

    resources: list[dict] = []
    for yaml_file in sorted(k8s_path.glob("*.yaml")):
        try:
            docs = [d for d in yaml.safe_load_all(yaml_file.read_text()) if d]
        except yaml.YAMLError:
            continue
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            kind = doc.get("kind", "")
            name = doc.get("metadata", {}).get("name", "")
            replicas = None
            if kind in ("Deployment", "StatefulSet", "ReplicaSet"):
                replicas = doc.get("spec", {}).get("replicas", 1)
            storage = None
            if kind == "PersistentVolumeClaim":
                storage = (
                    doc.get("spec", {})
                    .get("resources", {})
                    .get("requests", {})
                    .get("storage")
                )
            resources.append(
                {"kind": kind, "name": name, "replicas": replicas, "storage": storage}
            )

    return resources
