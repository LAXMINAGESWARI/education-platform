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
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, inspect, or_, select, update
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


_bound_lease: ContextVar[OwnedLease | None] = ContextVar("bound_lease", default=None)


def exceeded_max_attempts_message(max_attempts: int) -> str:
    """Failure text when a row can no longer be claimed."""
    return f"Job exceeded max attempts ({max_attempts})"


def bound_lease() -> OwnedLease | None:
    """Lease bound on this thread, if ``bind_lease`` is active."""
    return _bound_lease.get()


@contextmanager
def bind_lease(lease: OwnedLease) -> Iterator[OwnedLease]:
    """Bind ``lease`` for ``commit_owned`` on this thread.

    The heartbeat thread is started before this runs, so it does not inherit the
    token and keeps using its own conditional update.
    """
    token = _bound_lease.set(lease)
    try:
        yield lease
    finally:
        _bound_lease.reset(token)


def close_session(session: Session) -> None:
    """Close ``session``. A shutdown interrupt during close still propagates.

    ``run_forever`` catches that ``KeyboardInterrupt`` so the process exits
    cleanly instead of tracebacking out of ``session.close()``.
    """
    try:
        session.close()
    except KeyboardInterrupt:
        logger.info("Worker shutdown interrupted session close")
        raise


def prepare_leased_writes(session: Session) -> None:
    """Turn autoflush off while a lease is bound.

    A later SELECT would otherwise flush ``job.status = succeeded`` by primary
    key and overwrite the worker that reclaimed the row.
    """
    if _bound_lease.get() is not None:
        session.autoflush = False


def commit_owned(session: Session) -> bool:
    """Commit, or discard the transaction when this worker lost the lease.

    With no bound lease (tests, exhausted failures, the claim itself) this is
    ``session.commit()``. With a lease, job-column changes are applied with the
    same ``id`` / ``status='running'`` / ``attempts`` / ``locked_until`` match
    as ``release_lease``. A miss is logged, the transaction is rolled back, and
    the new owner's row is left unchanged.
    """
    lease = _bound_lease.get()
    if lease is None:
        session.commit()
        return True
    with lease.lock:
        if lease.lost:
            logger.info(
                "Discarded %s %s write; lease is no longer owned",
                lease.model.__name__,
                lease.job_id,
            )
            session.rollback()
            return False
        matched = _write_owned_row(session, lease)
        if not matched:
            logger.info(
                "Discarded %s %s write; lease is no longer owned",
                lease.model.__name__,
                lease.job_id,
            )
            session.rollback()
            lease.lost = True
            return False
        session.commit()
        return True


def _pending_job_values(job: Any) -> dict[str, Any]:
    insp = inspect(job)
    values: dict[str, Any] = {}
    for column in insp.mapper.column_attrs:
        key = column.key
        if key in {"id", "created_at", "updated_at"}:
            continue
        if insp.attrs[key].history.has_changes():
            values[key] = getattr(job, key)
    return values


def _write_owned_row(session: Session, lease: OwnedLease) -> bool:
    """Apply pending job columns with the ownership WHERE, without a primary-key flush.

    ``expire`` drops the pending ORM state after it has been copied, so a later
    ``commit`` cannot write ``succeeded`` by id. The object stays in the session
    and later attribute access reloads the committed row. ``synchronize_session``
    is off so the UPDATE does not compare that in-memory object to
    ``status='running'`` or put the new values back into the identity map.
    """
    with session.no_autoflush:
        job = session.get(lease.model, lease.job_id)
        values = _pending_job_values(job) if job is not None else {}
        if job is not None and values:
            session.expire(job)
        if not values:
            values = {"attempts": lease.attempts}
        result = session.execute(
            update(lease.model)
            .where(
                lease.model.id == lease.job_id,
                lease.model.status == _status(lease.model, "running"),
                lease.model.attempts == lease.attempts,
                lease.model.locked_until == lease.locked_until,
            )
            .values(**values),
            execution_options={"synchronize_session": False},
        )
    rowcount = getattr(result, "rowcount", None)
    return isinstance(rowcount, int) and rowcount > 0


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
    was_running = _is_running(job.status)
    if attempts_are_exhausted(int(job.attempts), int(job.max_attempts)):
        return Exhausted(job_id)
    # Same predicate as the status and stream reads. A running row here already
    # failed the SQL lease check, so treat its lock as missing. A queued row
    # resets only when it kept attempts >= 1. release_lease must call
    # reset_stage_on_requeue only when its update rowcount is 1.
    if getattr(model, "__tablename__", None) == "ingest_jobs" and lease_requires_stage_reset(
        job_status="running" if was_running else _status_value(job.status),
        attempts=int(job.attempts or 0),
        locked_until=None if was_running else job.locked_until,
    ):
        reset_stage_on_requeue(session, job)
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
        rowcount = getattr(result, "rowcount", None)
        matched = isinstance(rowcount, int) and rowcount > 0
        if matched and getattr(lease.model, "__tablename__", None) == "ingest_jobs":
            job = session.get(lease.model, lease.job_id)
            if job is not None:
                reset_stage_on_requeue(session, job)
        session.commit()
        return matched
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


def _status_value(status: Any) -> str:
    value = getattr(status, "value", status)
    return str(value)


def _is_running(status: Any) -> bool:
    return _status_value(status) == "running"
