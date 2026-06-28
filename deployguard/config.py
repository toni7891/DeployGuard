"""
Single config reader for the entire deployguard package.

No other module reads config files directly — everything calls get_config().
Priority order (highest → lowest):
  CLI flag → project config (.deployguard/config.yaml) → personal config
  (~/.deployguard/config.yaml) → built-in defaults
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


# ── Sub-models ────────────────────────────────────────────────────────────────

class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["local", "bedrock"] | None = None
    model: str = "qwen2.5-coder-14b-instruct"  # LM Studio model name when backend="local"
    temperature: float = 0.2


class GuardRulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_resource_limits: Literal["error", "warn", "off"] = "error"
    require_probes: Literal["error", "warn", "off"] = "error"
    require_security_context: Literal["error", "warn", "off"] = "error"
    no_latest_tag: Literal["error", "warn", "off"] = "error"
    no_root_user: Literal["error", "warn", "off"] = "error"
    no_privileged_containers: Literal["error", "warn", "off"] = "error"
    iam_least_privilege: Literal["error", "warn", "off"] = "warn"

    def as_dict(self) -> dict[str, str]:
        return self.model_dump()


class GuardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strict: bool = True
    explain: bool = True
    rules: GuardRulesConfig = Field(default_factory=GuardRulesConfig)
    custom_rules_dir: str | None = None


class CostConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = "USD"
    warn_threshold: float = 10.00
    reject_threshold: float = 50.00
    always_explain: bool = False


class DeployConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal["local", "aws"] = "local"
    error_rate_threshold: float = 1.0
    rollout_steps: list[int] = Field(default_factory=lambda: [10, 50, 100])
    smoke_timeout: int = 60
    audit: bool = True


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: LLMConfig = Field(default_factory=LLMConfig)
    guard: GuardConfig = Field(default_factory=GuardConfig)
    cost: CostConfig = Field(default_factory=CostConfig)
    deploy: DeployConfig = Field(default_factory=DeployConfig)


# ── Merge helpers ─────────────────────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base; lists replace, dicts recurse."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _filter_none(d: dict) -> dict:
    """Drop keys whose value is None (CLI flags that were not set)."""
    out: dict = {}
    for k, v in d.items():
        if isinstance(v, dict):
            filtered = _filter_none(v)
            if filtered:
                out[k] = filtered
        elif v is not None:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


# ── Public API ────────────────────────────────────────────────────────────────

def validate_config_file(path: Path) -> str | None:
    """Validate a single config file in isolation.

    Returns None on success, or a human-readable error string on failure.
    The caller is responsible for checking that the path exists.
    """
    try:
        raw = _load_yaml(path)
        merged = _deep_merge(AppConfig().model_dump(), raw)
        AppConfig(**merged)
        return None
    except yaml.YAMLError as exc:
        return f"YAML parse error: {exc}"
    except ValidationError as exc:
        errors = exc.errors()
        if errors:
            first = errors[0]
            loc = " → ".join(str(x) for x in first["loc"])
            return f"Invalid at '{loc}': {first['msg']}"
        return str(exc)


def load_config(
    cli_overrides: dict | None = None,
    _personal_path: Path | None = None,
    _project_path: Path | None = None,
) -> AppConfig:
    """Build AppConfig by merging defaults ← personal ← project ← CLI flags.

    _personal_path / _project_path are test-only escape hatches; production
    always uses the standard locations.
    """
    merged = AppConfig().model_dump()

    # 1. Personal config (~/.deployguard/config.yaml)
    personal = _personal_path or (Path.home() / ".deployguard" / "config.yaml")
    if personal.exists():
        merged = _deep_merge(merged, _load_yaml(personal))

    # 2. Project config (.deployguard/config.yaml relative to cwd)
    project = _project_path or (Path.cwd() / ".deployguard" / "config.yaml")
    if project.exists():
        merged = _deep_merge(merged, _load_yaml(project))

    # 3. CLI overrides — only set flags (non-None)
    if cli_overrides:
        merged = _deep_merge(merged, _filter_none(cli_overrides))

    # 4. Validate — raises ValidationError with field-level detail on bad keys/values
    return AppConfig(**merged)


# ── Singleton ─────────────────────────────────────────────────────────────────

_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reset_config() -> None:
    """Clear the cached config — for tests only."""
    global _config
    _config = None
