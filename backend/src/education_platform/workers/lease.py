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
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.db.session import sync_session

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
    if _is_running(job.status) and job.attempts >= job.max_attempts:
        return Exhausted(job_id)
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


def release_lease(model: type[Any], job_id: UUID) -> None:
    """Requeue a running job and clear its lease so another worker can claim it.

    There is no lease-owner column; ``locked_until`` is the lease. A row that
    is no longer ``running`` is left as the worker finished it.
    """
    session = sync_session()
    try:
        result = session.execute(
            update(model)
            .where(model.id == job_id, model.status == _status(model, "running"))
            .values(status=_status(model, "queued"), locked_until=None)
        )
        session.commit()
        if isinstance(result, CursorResult) and result.rowcount:
            logger.info("Requeued %s %s after shutdown", model.__name__, job_id)
    except Exception:
        logger.exception("Lease release failed for %s", job_id)
        session.rollback()
    finally:
        session.close()


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


def _is_running(status: Any) -> bool:
    value = status.value if isinstance(status, enum.Enum) else status
    return str(value) == "running"
