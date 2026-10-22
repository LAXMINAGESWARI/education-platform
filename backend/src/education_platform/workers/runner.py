"""Postgres-backed worker: claim queued or stale-running jobs with SKIP LOCKED."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from education_platform.db import models as _orm_tables
from education_platform.db.session import sync_session
from education_platform.modules.assessments.models import FeedbackSummaryJob
from education_platform.modules.generation.adk import CurriculumRunner
from education_platform.modules.generation.blind_solve import BlindSolver
from education_platform.modules.generation.curriculum_worker import (
    fail_exhausted_curriculum_job,
    process_curriculum_job_sync,
)
from education_platform.modules.generation.items import ItemsWriter
from education_platform.modules.generation.lesson import TopicLessonWriter
from education_platform.modules.generation.models import CurriculumGenerationJob, GenerationJob
from education_platform.modules.generation.outline import OutlineWriter
from education_platform.modules.generation.worker import (
    fail_exhausted_generation_job,
    process_generation_job_sync,
)
from education_platform.modules.rag.models import IngestJob
from education_platform.workers.feedback_summary import (
    fail_exhausted_feedback_summary_job,
    process_feedback_summary_job_sync,
)
from education_platform.workers.ingest import (
    ParsePdfFn,
    fail_exhausted_ingest_job,
    process_ingest_job_sync,
)
from education_platform.workers.lease import (
    Claimed,
    Exhausted,
    OwnedLease,
    bind_lease,
    claim,
    close_session,
    heartbeat,
)
from education_platform.workers.shutdown import WorkerShutdown

_ = _orm_tables  # register FK targets (users) for the worker process

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 1.5


def _sync_session() -> Session:
    return sync_session()


def claim_next_job(session: Session) -> UUID | None:
    """Claim one queued or stale-running ingest job.

    Marks the row ``running`` and commits so the lock is released before processing.
    A job that has already used its attempts is failed instead.
    """
    result = claim(session, IngestJob)
    if isinstance(result, Exhausted):
        fail_exhausted_ingest_job(session, result.job_id)
        return None
    if isinstance(result, Claimed):
        return result.job_id
    return None


class _ShutdownRequested:
    """Stop was set in the instant before a claim."""


_SHUTDOWN = _ShutdownRequested()


def _run_claimed(
    shutdown: WorkerShutdown | None,
    lease: OwnedLease,
    process: Callable[[], None],
) -> UUID | None:
    """Run one claimed job, or refund it when shutdown won the race.

    ``begin_job`` checks the stop flag under the same lock that records the
    lease. A signal that arrives after the claim and before that check releases
    the row immediately and refunds the attempt.
    """
    if shutdown is not None and not shutdown.begin_job(lease):
        shutdown.release_job(lease)
        return None
    try:
        process()
    finally:
        if shutdown is not None:
            shutdown.finish_job()
    return lease.job_id


def _claim_one(
    db: Session,
    model: type[Any],
    shutdown: WorkerShutdown | None,
    fail_exhausted: Callable[[Session, UUID], None],
    process: Callable[[UUID], None],
) -> UUID | None | _ShutdownRequested:
    """Claim one row, or return ``_SHUTDOWN`` without claiming when stop is set."""
    if shutdown is not None and shutdown.stop.is_set():
        return _SHUTDOWN
    result = claim(db, model)
    if result is None:
        return None
    if isinstance(result, Exhausted):
        fail_exhausted(db, result.job_id)
        return result.job_id
    lease = OwnedLease.from_claimed(model, result)

    def _run() -> None:
        # Enter heartbeat first so its thread does not copy the bound lease.
        with heartbeat(lease), bind_lease(lease):
            process(lease.job_id)

    return _run_claimed(shutdown, lease, _run)


def poll_once(
    *,
    parse_pdf: ParsePdfFn | None = None,
    write_outline: OutlineWriter | None = None,
    write_items: ItemsWriter | None = None,
    write_lesson: TopicLessonWriter | None = None,
    curriculum_runner: CurriculumRunner | None = None,
    blind_solver: BlindSolver | None = None,
    session: Session | None = None,
    shutdown: WorkerShutdown | None = None,
) -> UUID | None:
    """Claim at most one feedback, ingest, topic-generation, or curriculum job.

    Feedback prose is short and student-visible, so it is claimed before a PDF
    ingest that can hold the worker for a long parse. Once shutdown is requested,
    this returns without opening a session. Each claim also rechecks the stop
    flag immediately before ``claim()``.
    """
    if shutdown is not None and shutdown.stop.is_set():
        return None
    owns_session = session is None
    db = session or _sync_session()
    try:
        feedback = _claim_one(
            db,
            FeedbackSummaryJob,
            shutdown,
            fail_exhausted_feedback_summary_job,
            process_feedback_summary_job_sync,
        )
        if isinstance(feedback, _ShutdownRequested):
            return None
        if feedback is not None:
            return feedback
        ingest = _claim_one(
            db,
            IngestJob,
            shutdown,
            fail_exhausted_ingest_job,
            lambda job_id: process_ingest_job_sync(str(job_id), parse_pdf=parse_pdf),
        )
        if isinstance(ingest, _ShutdownRequested):
            return None
        if ingest is not None:
            return ingest
        generation = _claim_one(
            db,
            GenerationJob,
            shutdown,
            fail_exhausted_generation_job,
            lambda job_id: process_generation_job_sync(
                job_id,
                write_outline=write_outline,
                write_items=write_items,
                write_lesson=write_lesson,
                blind_solver=blind_solver,
            ),
        )
        if isinstance(generation, _ShutdownRequested):
            return None
        if generation is not None:
            return generation
        if curriculum_runner is None:
            return None
        curriculum = _claim_one(
            db,
            CurriculumGenerationJob,
            shutdown,
            fail_exhausted_curriculum_job,
            lambda job_id: process_curriculum_job_sync(job_id, runner=curriculum_runner),
        )
        if isinstance(curriculum, _ShutdownRequested):
            return None
        return curriculum
    finally:
        if owns_session:
            close_session(db)


def run_forever(
    *,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    shutdown: WorkerShutdown | None = None,
    poll: Callable[[], UUID | None] | None = None,
) -> None:
    """Poll feedback summaries, then ingest jobs, then generation jobs.

    SIGTERM and SIGINT stop new claims. The current job may finish inside
    ``WORKER_SHUTDOWN_GRACE_SECONDS``. Otherwise its lease is requeued (the
    attempt is refunded) and the in-flight job is cancelled so its ``finally``
    blocks run. The process then returns and exits 0.
    """
    state = shutdown if shutdown is not None else WorkerShutdown()
    if shutdown is None:
        state.install_signals()
    logging.basicConfig(level=logging.INFO)
    logger.info(
        "Worker started (poll every %.1fs; feedback, then ingest, then generation)",
        poll_interval_seconds,
    )
    try:
        while not state.stop.is_set():
            try:
                claimed = poll() if poll is not None else poll_once(shutdown=state)
                if state.stop.is_set():
                    break
                if claimed is None and state.stop.wait(poll_interval_seconds):
                    break
            except KeyboardInterrupt:
                _log_shutdown(state)
                return
    except KeyboardInterrupt:
        _log_shutdown(state)


def _log_shutdown(state: WorkerShutdown) -> None:
    """Say whether the release matched. A miss must not be logged as a requeue."""
    if state.release_matched is True:
        logger.info("Worker shutdown cancelled the in-flight job after releasing its lease")
        return
    if state.release_matched is False:
        logger.info("Worker shutdown cancelled the in-flight job; its lease release did not match")
        return
    logger.info("Worker shutdown cancelled the in-flight job")
