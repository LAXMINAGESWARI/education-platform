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
from education_platform.workers.lease import Claimed, Exhausted, claim, heartbeat
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


def _run_claimed(
    shutdown: WorkerShutdown | None,
    model: type[Any],
    job_id: UUID,
    process: Callable[[], None],
) -> UUID | None:
    """Run one claimed job, or put it back when shutdown already started."""
    if shutdown is not None and shutdown.stop.is_set():
        shutdown.release_job(model, job_id)
        return None
    if shutdown is None:
        process()
        return job_id
    shutdown.begin_job(model, job_id)
    try:
        process()
    finally:
        shutdown.finish_job()
    return job_id


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
    this returns without claiming.
    """
    if shutdown is not None and shutdown.stop.is_set():
        return None
    owns_session = session is None
    db = session or _sync_session()
    try:
        feedback = claim(db, FeedbackSummaryJob)
        if isinstance(feedback, Exhausted):
            fail_exhausted_feedback_summary_job(db, feedback.job_id)
            return feedback.job_id
        if isinstance(feedback, Claimed):
            feedback_id = feedback.job_id

            def _run_feedback() -> None:
                with heartbeat(FeedbackSummaryJob, feedback_id):
                    process_feedback_summary_job_sync(feedback_id)

            return _run_claimed(shutdown, FeedbackSummaryJob, feedback_id, _run_feedback)
        ingest = claim(db, IngestJob)
        if isinstance(ingest, Exhausted):
            fail_exhausted_ingest_job(db, ingest.job_id)
            return ingest.job_id
        if isinstance(ingest, Claimed):
            ingest_id = ingest.job_id

            def _run_ingest() -> None:
                with heartbeat(IngestJob, ingest_id):
                    process_ingest_job_sync(str(ingest_id), parse_pdf=parse_pdf)

            return _run_claimed(shutdown, IngestJob, ingest_id, _run_ingest)
        generation = claim(db, GenerationJob)
        if isinstance(generation, Exhausted):
            fail_exhausted_generation_job(db, generation.job_id)
            return generation.job_id
        if isinstance(generation, Claimed):
            generation_id = generation.job_id

            def _run_generation() -> None:
                with heartbeat(GenerationJob, generation_id):
                    process_generation_job_sync(
                        generation_id,
                        write_outline=write_outline,
                        write_items=write_items,
                        write_lesson=write_lesson,
                        blind_solver=blind_solver,
                    )

            return _run_claimed(shutdown, GenerationJob, generation_id, _run_generation)
        if curriculum_runner is not None:
            curriculum = claim(db, CurriculumGenerationJob)
            if isinstance(curriculum, Exhausted):
                fail_exhausted_curriculum_job(db, curriculum.job_id)
                return curriculum.job_id
            if isinstance(curriculum, Claimed):
                curriculum_id = curriculum.job_id

                def _run_curriculum() -> None:
                    with heartbeat(CurriculumGenerationJob, curriculum_id):
                        process_curriculum_job_sync(curriculum_id, runner=curriculum_runner)

                return _run_claimed(
                    shutdown, CurriculumGenerationJob, curriculum_id, _run_curriculum
                )
        return None
    finally:
        if owns_session:
            db.close()


def run_forever(
    *,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    shutdown: WorkerShutdown | None = None,
    poll: Callable[[], UUID | None] | None = None,
) -> None:
    """Poll feedback summaries, then ingest jobs, then generation jobs.

    SIGTERM and SIGINT stop new claims. The current job may finish inside
    ``WORKER_SHUTDOWN_GRACE_SECONDS``; otherwise its lease is requeued and the
    process exits 0.
    """
    state = shutdown if shutdown is not None else WorkerShutdown()
    if shutdown is None:
        state.install_signals()
    logging.basicConfig(level=logging.INFO)
    logger.info(
        "Worker started (poll every %.1fs; feedback, then ingest, then generation)",
        poll_interval_seconds,
    )
    while not state.stop.is_set():
        claimed = poll() if poll is not None else poll_once(shutdown=state)
        if state.stop.is_set():
            break
        if claimed is None and state.stop.wait(poll_interval_seconds):
            break
