"""Claim queued jobs and take back ones left stuck in ``running``."""

from __future__ import annotations

import enum
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.db.session import sync_session
from education_platform.modules.progress.stage_store import (
    lease_requires_stage_reset,
    reset_stage_on_requeue,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Claimed:
    job_id: UUID


@dataclass(frozen=True, slots=True)
class Exhausted:
    job_id: UUID


ClaimResult = Claimed | Exhausted


def claim(session: Session, model: type[Any]) -> ClaimResult | None:
    """Lock one queued job, or one running job whose lease is missing or expired.

    A stale job that has already used ``max_attempts`` is returned as
    ``Exhausted`` and left for the caller to fail. Every other claim increments
    ``attempts``, marks the row running, and sets ``locked_until``.
    """
    queued = _status(model, "queued")
    running = _status(model, "running")
    job = session.scalar(
        select(model)
        .where(
            or_(
                model.status == queued,
                and_(
                    model.status == running,
                    or_(model.locked_until.is_(None), model.locked_until < func.now()),
                ),
            )
        )
        .order_by(model.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job_id = job.id
    was_running = _is_running(job.status)
    if was_running and job.attempts >= job.max_attempts:
        return Exhausted(job_id)
    # Same predicate as the status and stream reads. A running row here already
    # failed the SQL lease check, so treat its lock as missing. A queued row
    # resets only when it kept attempts >= 1. release_lease (PR #200, 2a00407)
    # must call reset_stage_on_requeue only when its update rowcount is 1.
    if getattr(model, "__tablename__", None) == "ingest_jobs" and lease_requires_stage_reset(
        job_status="running" if was_running else _status_value(job.status),
        attempts=int(job.attempts or 0),
        locked_until=None if was_running else job.locked_until,
    ):
        reset_stage_on_requeue(session, job)
    lease = get_settings().worker_lease_seconds
    job.attempts += 1
    job.status = running
    job.locked_until = datetime.now(UTC) + timedelta(seconds=lease)
    session.commit()
    return Claimed(job_id)


@contextmanager
def heartbeat(model: type[Any], job_id: UUID) -> Iterator[None]:
    """Extend ``locked_until`` while ``job_id`` is still running."""
    interval = get_settings().worker_lease_seconds / 3
    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(interval):
            _extend_lease(model, job_id)

    thread = threading.Thread(target=_beat, name=f"lease-{job_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def _extend_lease(model: type[Any], job_id: UUID) -> None:
    lease = get_settings().worker_lease_seconds
    session = sync_session()
    try:
        session.execute(
            update(model)
            .where(model.id == job_id, model.status == _status(model, "running"))
            .values(locked_until=func.now() + timedelta(seconds=lease))
        )
        session.commit()
    except Exception:
        logger.exception("Lease heartbeat failed for %s", job_id)
        session.rollback()
    finally:
        session.close()


def _status(model: type[Any], value: str) -> Any:
    enum_class = model.__table__.c.status.type.enum_class
    return enum_class(value)


def _status_value(status: Any) -> str:
    value = status.value if isinstance(status, enum.Enum) else status
    return str(value)


def _is_running(status: Any) -> bool:
    return _status_value(status) == "running"
