"""Claim and process admin ADK curriculum generation jobs."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.db.session import sync_session
from education_platform.modules.generation.adk import (
    MISSING_OPENROUTER_MESSAGE,
    NO_INGESTED_CHUNKS_MESSAGE,
    CurriculumRunner,
)
from education_platform.modules.generation.curriculum import (
    build_adk_request,
    fail_curriculum_job,
    ingested_chunks_for_subtopic,
    persist_approved_curriculum,
)
from education_platform.modules.generation.models import CurriculumGenerationJob
from education_platform.modules.generation.types import GenerationJobStatus
from education_platform.workers.lease import (
    Claimed,
    Exhausted,
    claim,
    commit_owned,
    exceeded_max_attempts_message,
    prepare_leased_writes,
)

logger = logging.getLogger(__name__)


def claim_next_curriculum_job(session: Session) -> UUID | None:
    """Claim one queued or stale-running curriculum job. Exhausted jobs are failed."""
    result = claim(session, CurriculumGenerationJob)
    if isinstance(result, Exhausted):
        fail_exhausted_curriculum_job(session, result.job_id)
        return None
    if isinstance(result, Claimed):
        return result.job_id
    return None


def fail_exhausted_curriculum_job(session: Session, job_id: UUID) -> None:
    job = session.get(CurriculumGenerationJob, job_id)
    if job is None:
        return
    fail_curriculum_job(job, exceeded_max_attempts_message(int(job.max_attempts)))
    commit_owned(session)


def _resolve_runner(runner: CurriculumRunner | None) -> CurriculumRunner | str:
    if runner is not None:
        return runner
    if not get_settings().openrouter_configured:
        return MISSING_OPENROUTER_MESSAGE
    # Live ADK is imported only when a job actually needs the model, so unit tests
    # that inject a fake CurriculumRunner never construct google.adk agents.
    from education_platform.modules.generation.adk_live import LiveAdkRunner

    return LiveAdkRunner()


def process_curriculum_job_sync(
    job_id: UUID,
    *,
    runner: CurriculumRunner | None = None,
) -> None:
    session = sync_session()
    prepare_leased_writes(session)
    try:
        job = session.get(CurriculumGenerationJob, job_id)
        if job is None:
            logger.error("Curriculum generation job %s not found", job_id)
            return
        job.status = GenerationJobStatus.RUNNING
        commit_owned(session)
        chunks = ingested_chunks_for_subtopic(session, job.subtopic_id)
        if not chunks:
            fail_curriculum_job(job, NO_INGESTED_CHUNKS_MESSAGE)
            commit_owned(session)
            return
        resolved = _resolve_runner(runner)
        if isinstance(resolved, str):
            fail_curriculum_job(job, resolved)
            commit_owned(session)
            return
        request = build_adk_request(session, job, chunks)
        if isinstance(request, str):
            fail_curriculum_job(job, request)
            commit_owned(session)
            return
        result = resolved.generate(request)
        job.round_count = result.round_count
        job.reviewer_notes = result.reviewer_notes or None
        job.transcript = result.transcript
        gate = persist_approved_curriculum(session, job, result)
        if gate is not None:
            session.rollback()
            job = session.get(CurriculumGenerationJob, job_id)
            if job is None:
                return
            fail_curriculum_job(job, gate, result=result)
        commit_owned(session)
    except Exception as exc:
        logger.exception("Curriculum generation job %s failed", job_id)
        session.rollback()
        job = session.get(CurriculumGenerationJob, job_id)
        if job is not None:
            fail_curriculum_job(job, str(exc)[:2000])
            commit_owned(session)
    finally:
        session.close()
