"""Claim queued jobs and take back ones left stuck in ``running``.

Attempt semantics:

* A claim increments ``attempts`` and writes ``locked_until``. Queued rows and
  stale running rows both stop at ``max_attempts`` (``Exhausted``). A crash
  does not refund, so a crash loop cannot run forever.
* A graceful SIGTERM release refunds that increment in the same UPDATE that
  requeues the row. Repeated deploys therefore stay under ``max_attempts``.
* The release and the heartbeat match ``attempts`` plus the ``locked_until``
  this worker last wrote. There is no owner column. A miss is a logged no-op,
  and the heartbeat stops extending a lease it no longer owns.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.db.session import sync_session

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Claimed:
    job_id: UUID
    attempts: int
    locked_until: datetime


@dataclass(frozen=True, slots=True)
class Exhausted:
    job_id: UUID


ClaimResult = Claimed | Exhausted


@dataclass
class OwnedLease:
    """Lease token for the worker that claimed ``job_id``.

    ``locked_until`` is the value this worker last wrote. Heartbeat and release
    share ``lock`` so a release cannot use a token the heartbeat has already replaced.
    """

    model: type[Any]
    job_id: UUID
    attempts: int
    locked_until: datetime
    lost: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @classmethod
    def from_claimed(cls, model: type[Any], claimed: Claimed) -> OwnedLease:
        return cls(
            model=model,
            job_id=claimed.job_id,
            attempts=claimed.attempts,
            locked_until=claimed.locked_until,
        )


def attempts_are_exhausted(attempts: int, max_attempts: int) -> bool:
    """True when another run would pass ``max_attempts``.

    Applies to queued rows and to stale running rows. A graceful release
    decrements ``attempts``; a crash does not.
    """
    return attempts >= max_attempts


def claim(session: Session, model: type[Any]) -> ClaimResult | None:
    """Lock one queued job, or one running job whose lease is missing or expired.

    A row that has already used ``max_attempts`` is returned as ``Exhausted``
    and left for the caller to fail, whether it is queued or still marked
    running. Every other claim increments ``attempts``, marks the row running,
    and sets ``locked_until``.
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
    if attempts_are_exhausted(int(job.attempts), int(job.max_attempts)):
        return Exhausted(job_id)
    lease_seconds = get_settings().worker_lease_seconds
    attempts = int(job.attempts) + 1
    locked_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
    job.attempts = attempts
    job.status = running
    job.locked_until = locked_until
    session.commit()
    session.refresh(job)
    stored_until = job.locked_until
    if stored_until is None:
        raise RuntimeError("claim did not persist locked_until")
    return Claimed(job_id=job_id, attempts=int(job.attempts), locked_until=stored_until)


@contextmanager
def heartbeat(lease: OwnedLease) -> Iterator[OwnedLease]:
    """Extend ``locked_until`` while this worker still owns ``lease``."""
    interval = get_settings().worker_lease_seconds / 3
    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(interval):
            if not _extend_lease(lease):
                return

    thread = threading.Thread(target=_beat, name=f"lease-{lease.job_id}", daemon=True)
    thread.start()
    try:
        yield lease
    finally:
        stop.set()
        thread.join(timeout=5)


def release_lease(lease: OwnedLease) -> bool:
    """Requeue a job this worker still owns and refund the claim's attempt.

    The UPDATE matches ``id``, ``attempts``, ``status='running'``, and the
    ``locked_until`` value this worker last wrote. A miss is logged and leaves
    the row unchanged.
    """
    with lease.lock:
        if lease.lost:
            logger.info(
                "Shutdown release left %s %s unchanged; lease is no longer owned",
                lease.model.__name__,
                lease.job_id,
            )
            return False
        matched = _release_owned_row(lease)
        lease.lost = True
        if matched:
            logger.info(
                "Requeued %s %s and refunded an attempt",
                lease.model.__name__,
                lease.job_id,
            )
        else:
            logger.info(
                "Shutdown release left %s %s unchanged; lease is no longer owned",
                lease.model.__name__,
                lease.job_id,
            )
        return matched


def _release_owned_row(lease: OwnedLease) -> bool:
    session = sync_session()
    try:
        result = session.execute(
            update(lease.model)
            .where(
                lease.model.id == lease.job_id,
                lease.model.status == _status(lease.model, "running"),
                lease.model.attempts == lease.attempts,
                lease.model.locked_until == lease.locked_until,
            )
            .values(
                status=_status(lease.model, "queued"),
                locked_until=None,
                attempts=lease.model.attempts - 1,
            )
        )
        session.commit()
        rowcount = getattr(result, "rowcount", None)
        return isinstance(rowcount, int) and rowcount > 0
    except Exception:
        logger.exception("Lease release failed for %s", lease.job_id)
        session.rollback()
        return False
    finally:
        session.close()


def _extend_lease(lease: OwnedLease) -> bool:
    """Write a new ``locked_until`` if we still own the row.

    False stops the heartbeat: the row no longer matches this worker's token.
    A database error returns True so the next interval tries again.
    """
    lease_seconds = get_settings().worker_lease_seconds
    new_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
    with lease.lock:
        if lease.lost:
            return False
        session = sync_session()
        try:
            result = session.execute(
                update(lease.model)
                .where(
                    lease.model.id == lease.job_id,
                    lease.model.status == _status(lease.model, "running"),
                    lease.model.attempts == lease.attempts,
                    lease.model.locked_until == lease.locked_until,
                )
                .values(locked_until=new_until)
                .returning(lease.model.locked_until)
            )
            stored = result.scalar_one_or_none()
            session.commit()
        except Exception:
            logger.exception("Lease heartbeat failed for %s", lease.job_id)
            session.rollback()
            return True
        finally:
            session.close()
        if stored is None:
            lease.lost = True
            logger.info(
                "Heartbeat stopped for %s %s; lease is no longer owned",
                lease.model.__name__,
                lease.job_id,
            )
            return False
        lease.locked_until = stored
        return True


def _status(model: type[Any], value: str) -> Any:
    enum_class = model.__table__.c.status.type.enum_class
    return enum_class(value)
