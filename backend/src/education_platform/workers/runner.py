"""Postgres-backed worker: claim queued or stale-running jobs with SKIP LOCKED."""

from __future__ import annotations

import logging
import time
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


def poll_once(
    *,
    parse_pdf: ParsePdfFn | None = None,
    write_outline: OutlineWriter | None = None,
    write_items: ItemsWriter | None = None,
    write_lesson: TopicLessonWriter | None = None,
    curriculum_runner: CurriculumRunner | None = None,
    blind_solver: BlindSolver | None = None,
    session: Session | None = None,
) -> UUID | None:
    """Claim at most one feedback, ingest, topic-generation, or curriculum job.

    Feedback prose is short and student-visible, so it is claimed before a PDF
    ingest that can hold the worker for a long parse.
    """
    owns_session = session is None
    db = session or _sync_session()
    try:
        feedback = claim(db, FeedbackSummaryJob)
        if isinstance(feedback, Exhausted):
            fail_exhausted_feedback_summary_job(db, feedback.job_id)
            return feedback.job_id
        if isinstance(feedback, Claimed):
            with heartbeat(FeedbackSummaryJob, feedback.job_id):
                process_feedback_summary_job_sync(feedback.job_id)
            return feedback.job_id
        ingest = claim(db, IngestJob)
        if isinstance(ingest, Exhausted):
            fail_exhausted_ingest_job(db, ingest.job_id)
            return ingest.job_id
        if isinstance(ingest, Claimed):
            with heartbeat(IngestJob, ingest.job_id):
                process_ingest_job_sync(str(ingest.job_id), parse_pdf=parse_pdf)
            return ingest.job_id
        generation = claim(db, GenerationJob)
        if isinstance(generation, Exhausted):
            fail_exhausted_generation_job(db, generation.job_id)
            return generation.job_id
        if isinstance(generation, Claimed):
            with heartbeat(GenerationJob, generation.job_id):
                process_generation_job_sync(
                    generation.job_id,
                    write_outline=write_outline,
                    write_items=write_items,
                    write_lesson=write_lesson,
                    blind_solver=blind_solver,
                )
            return generation.job_id
        if curriculum_runner is not None:
            curriculum = claim(db, CurriculumGenerationJob)
            if isinstance(curriculum, Exhausted):
                fail_exhausted_curriculum_job(db, curriculum.job_id)
                return curriculum.job_id
            if isinstance(curriculum, Claimed):
                with heartbeat(CurriculumGenerationJob, curriculum.job_id):
                    process_curriculum_job_sync(curriculum.job_id, runner=curriculum_runner)
                return curriculum.job_id
        return None
    finally:
        if owns_session:
            db.close()


def run_forever(*, poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
    """Poll feedback summaries, then ingest jobs, then generation jobs."""
    logging.basicConfig(level=logging.INFO)
    logger.info(
        "Worker started (poll every %.1fs; feedback, then ingest, then generation)",
        poll_interval_seconds,
    )
    while True:
        claimed = poll_once()
        if claimed is None:
            time.sleep(poll_interval_seconds)
