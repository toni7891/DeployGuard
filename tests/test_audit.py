"""Tests for engine/audit.py — model, write_audit, try_write_audit."""
from __future__ import annotations

import warnings
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from deployguard.engine.audit import Base, DeployAudit, get_db_session, try_write_audit, write_audit


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def session():
    """SQLite in-memory session with audit table created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sample_fields(**overrides) -> dict:
    base = {
        "service": "payments-api",
        "image_tag": "abc1234",
        "target": "local",
        "started_at": _now(),
        "finished_at": _now(),
        "result": "success",
        "reason": None,
        "rollout_steps": [{"weight": 10, "error_rate": 0.1}, {"weight": 50, "error_rate": 0.2}],
        "operator": "dev@example.com",
    }
    return {**base, **overrides}


# ── write_audit ────────────────────────────────────────────────────────────────

def test_write_audit_creates_row(session):
    row = write_audit(session, **_sample_fields())
    assert row.id is not None
    assert row.service == "payments-api"
    assert row.image_tag == "abc1234"
    assert row.target == "local"
    assert row.result == "success"
    assert row.reason is None
    assert row.operator == "dev@example.com"


def test_write_audit_stores_rollout_steps_as_json(session):
    steps = [{"weight": 10, "error_rate": 0.0, "ts": "2026-01-01T00:00:00Z"}]
    row = write_audit(session, **_sample_fields(rollout_steps=steps))
    assert row.rollout_steps == steps


def test_write_audit_allows_null_reason(session):
    row = write_audit(session, **_sample_fields(reason=None))
    assert row.reason is None


def test_write_audit_rollback_result(session):
    row = write_audit(session, **_sample_fields(result="rollback", reason="error rate 5.2% > 1%"))
    assert row.result == "rollback"
    assert "5.2%" in row.reason


def test_write_audit_precheck_failed_result(session):
    row = write_audit(session, **_sample_fields(result="precheck_failed", reason="Readiness timeout"))
    assert row.result == "precheck_failed"


def test_write_audit_persists_across_queries(session):
    write_audit(session, **_sample_fields(service="svc-a"))
    write_audit(session, **_sample_fields(service="svc-b"))
    rows = session.query(DeployAudit).all()
    assert len(rows) == 2
    assert {r.service for r in rows} == {"svc-a", "svc-b"}


# ── get_db_session ─────────────────────────────────────────────────────────────

def test_get_db_session_creates_table_and_yields_session():
    with get_db_session("sqlite:///:memory:") as s:
        assert isinstance(s, Session)
        row = write_audit(s, **_sample_fields())
        assert row.id == 1


# ── try_write_audit ────────────────────────────────────────────────────────────

def test_try_write_audit_warns_when_no_database_url():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try_write_audit(None, **_sample_fields())
    assert any("DATABASE_URL" in str(w.message) for w in caught)


def test_try_write_audit_writes_row_with_valid_url():
    with get_db_session("sqlite:///:memory:") as session_check:
        try_write_audit("sqlite:///:memory:", **_sample_fields())
        # The try_write_audit opens its own engine; just verify no exception raised
        pass


def test_try_write_audit_warns_on_bad_url():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try_write_audit(
            "postgresql://nonexistent:5432/nodb",
            **_sample_fields(),
        )
    assert any("Audit write failed" in str(w.message) for w in caught)


def test_try_write_audit_does_not_raise_on_bad_url():
    # Should log warning and continue — never propagate the exception
    try_write_audit("postgresql://nonexistent:5432/nodb", **_sample_fields())
