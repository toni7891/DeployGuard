"""Tests for config.py — priority order, schema validation, reset."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from deployguard.config import get_config, load_config, reset_config


@pytest.fixture(autouse=True)
def clear_cache():
    reset_config()
    yield
    reset_config()


# ── Defaults ──────────────────────────────────────────────────────────────────

def test_defaults():
    config = load_config()
    assert config.deploy.target == "local"
    assert config.guard.strict is True
    assert config.guard.explain is True
    assert config.deploy.rollout_steps == [10, 50, 100]
    assert config.deploy.error_rate_threshold == 1.0
    assert config.cost.warn_threshold == 10.0
    assert config.cost.reject_threshold == 50.0
    assert config.llm.backend is None


# ── Priority: personal < project ─────────────────────────────────────────────

def test_personal_config_overrides_defaults(tmp_path: Path):
    personal = tmp_path / "personal.yaml"
    personal.write_text(yaml.dump({"deploy": {"target": "aws"}}))

    config = load_config(_personal_path=personal)
    assert config.deploy.target == "aws"
    assert config.guard.strict is True  # default preserved


def test_project_config_overrides_personal(tmp_path: Path):
    personal = tmp_path / "personal.yaml"
    personal.write_text(yaml.dump({"guard": {"strict": False}}))

    project = tmp_path / "project.yaml"
    project.write_text(yaml.dump({"guard": {"strict": True}}))

    config = load_config(_personal_path=personal, _project_path=project)
    assert config.guard.strict is True


def test_deep_merge_preserves_sibling_keys(tmp_path: Path):
    """Project overriding one sub-key must not wipe siblings from personal."""
    personal = tmp_path / "personal.yaml"
    personal.write_text(yaml.dump({"guard": {"strict": False, "explain": False}}))

    project = tmp_path / "project.yaml"
    project.write_text(yaml.dump({"guard": {"strict": True}}))

    config = load_config(_personal_path=personal, _project_path=project)
    assert config.guard.strict is True    # overridden by project
    assert config.guard.explain is False  # preserved from personal


# ── Priority: cli_overrides > project ────────────────────────────────────────

def test_cli_overrides_project(tmp_path: Path):
    project = tmp_path / "project.yaml"
    project.write_text(yaml.dump({"deploy": {"target": "aws"}}))

    config = load_config(
        cli_overrides={"deploy": {"target": "local"}},
        _project_path=project,
    )
    assert config.deploy.target == "local"


def test_cli_none_values_are_ignored(tmp_path: Path):
    """None in cli_overrides must not clobber lower-priority values."""
    project = tmp_path / "project.yaml"
    project.write_text(yaml.dump({"deploy": {"target": "aws"}}))

    config = load_config(
        cli_overrides={"deploy": {"target": None}},
        _project_path=project,
    )
    assert config.deploy.target == "aws"


# ── Missing files ─────────────────────────────────────────────────────────────

def test_missing_config_files_silently_skipped(tmp_path: Path):
    nonexistent = tmp_path / "does_not_exist.yaml"
    config = load_config(_personal_path=nonexistent, _project_path=nonexistent)
    assert config.deploy.target == "local"


# ── Validation ────────────────────────────────────────────────────────────────

def test_invalid_config_key_raises(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.dump({"deploy": {"nonexistent_key": "value"}}))

    with pytest.raises(ValidationError):
        load_config(_project_path=bad)


def test_invalid_config_value_raises(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.dump({"deploy": {"target": "not_a_valid_target"}}))

    with pytest.raises(ValidationError):
        load_config(_project_path=bad)


def test_invalid_guard_rule_level_raises(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        yaml.dump({"guard": {"rules": {"require_resource_limits": "block"}}})
    )

    with pytest.raises(ValidationError):
        load_config(_project_path=bad)


# ── Singleton cache ───────────────────────────────────────────────────────────

def test_get_config_returns_same_instance():
    c1 = get_config()
    c2 = get_config()
    assert c1 is c2


def test_reset_config_clears_cache():
    c1 = get_config()
    reset_config()
    c2 = get_config()
    assert c1 is not c2
