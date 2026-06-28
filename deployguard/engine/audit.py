"""Audit log — writes a deploy record to Postgres (or any SQLAlchemy-compatible DB)."""
from __future__ import annotations

import os
import subprocess
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedColumn, Session, mapped_column


# ── ORM model ─────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class DeployAudit(Base):
    __tablename__ = "deploy_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service: Mapped[str] = mapped_column(String(255))
    image_tag: Mapped[str] = mapped_column(String(255))
    target: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime] = mapped_column(DateTime)
    result: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollout_steps: Mapped[list | None] = mapped_column(JSON, nullable=True)
    operator: Mapped[str] = mapped_column(String(255))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_operator() -> str:
    """Return git user.email, falling back to $USER, then 'unknown'."""
    result = subprocess.run(
        ["git", "config", "user.email"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return os.environ.get("USER", "unknown")


# ── Public API ─────────────────────────────────────────────────────────────────

@contextmanager
def get_db_session(database_url: str) -> Generator[Session, None, None]:
    """Yield a SQLAlchemy Session; creates the audit table if it does not exist."""
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def write_audit(session: Session, **fields) -> DeployAudit:
    """Insert a DeployAudit row using the provided keyword fields and commit."""
    row = DeployAudit(**fields)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def try_write_audit(
    database_url: str | None,
    *,
    service: str,
    image_tag: str,
    target: str,
    started_at: datetime,
    finished_at: datetime,
    result: str,
    reason: str | None = None,
    rollout_steps: list | None = None,
    operator: str | None = None,
) -> None:
    """Best-effort audit write — logs a warning and continues on any failure.

    Pass database_url=None to skip silently (DATABASE_URL not configured).
    """
    if not database_url:
        warnings.warn(
            "DATABASE_URL not set — audit record skipped. "
            "Set DATABASE_URL to enable audit logging.",
            stacklevel=2,
        )
        return

    fields = {
        "service": service,
        "image_tag": image_tag,
        "target": target,
        "started_at": started_at,
        "finished_at": finished_at,
        "result": result,
        "reason": reason,
        "rollout_steps": rollout_steps,
        "operator": operator or _get_operator(),
    }

    try:
        with get_db_session(database_url) as session:
            write_audit(session, **fields)
    except Exception as exc:
        warnings.warn(
            f"Audit write failed (non-blocking): {exc}",
            stacklevel=2,
        )
