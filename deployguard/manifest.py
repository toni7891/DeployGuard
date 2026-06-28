from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, field_validator


class ServiceManifest(BaseModel):
    name: str
    port: int = 8000
    replicas: int = 2
    health_liveness: str = "/healthz"
    health_readiness: str = "/readyz"
    image: str | None = None
    namespace: str = "default"

    @field_validator("name")
    @classmethod
    def name_must_be_valid(cls, v: str) -> str:
        if not v:
            raise ValueError("name cannot be empty")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", v):
            raise ValueError(
                f"name '{v}' must start with a lowercase letter and contain only "
                "lowercase letters, digits, and hyphens (valid Kubernetes name)"
            )
        return v

    @field_validator("port")
    @classmethod
    def port_must_be_valid(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"port {v} must be between 1 and 65535")
        return v


def load_manifest(path: str = "deployguard.yaml") -> ServiceManifest:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"No deployguard.yaml found at '{path}'. "
            "Run 'dg init <name>' to create one."
        )

    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"deployguard.yaml is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("deployguard.yaml must be a YAML mapping, not a list or scalar.")

    try:
        return ServiceManifest(**data)
    except ValidationError as exc:
        # Convert Pydantic errors into a single human-readable ValueError
        messages = []
        for err in exc.errors():
            field = ".".join(str(loc) for loc in err["loc"]) if err["loc"] else "?"
            messages.append(f"  '{field}': {err['msg']}")
        raise ValueError(
            "deployguard.yaml has schema errors:\n" + "\n".join(messages)
        ) from exc


def write_manifest(manifest: ServiceManifest, path: str = "deployguard.yaml") -> None:
    data = manifest.model_dump(exclude_none=True)
    Path(path).write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
