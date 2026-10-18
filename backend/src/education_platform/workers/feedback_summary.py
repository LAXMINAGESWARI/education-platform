"""Claim and finish feedback-summary jobs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from education_platform.core.llm import OpenRouterError
from education_platform.db.session import sync_session
from education_platform.modules.assessments import feedback_summary
from education_platform.modules.assessments.models import (
    FeedbackSummaryJob,
    FeedbackSummaryJobStatus,
    FeedbackSummaryOrigin,
)

logger = logging.getLogger(__name__)


def process_feedback_summary_job_sync(job_id: UUID) -> None:
    """Generate prose for a claimed job and store it when the hash is still current."""
    session = sync_session()
    try:
        job = session.get(FeedbackSummaryJob, job_id)
        if job is None or job.status != FeedbackSummaryJobStatus.RUNNING:
            return
        try:
            text = feedback_summary.write_summary(job.prompt)
        except OpenRouterError as exc:
            logger.warning("feedback summary generation failed: %s", exc)
            text = ""
        if text.strip():
            feedback_summary.apply_summary(session, job, text.strip(), FeedbackSummaryOrigin.MODEL)
        else:
            feedback_summary.apply_summary(
                session, job, job.template_summary, FeedbackSummaryOrigin.TEMPLATE
            )
        job.status = FeedbackSummaryJobStatus.SUCCEEDED
        job.error = None
        session.commit()
    except Exception:
        logger.exception("feedback summary job %s failed", job_id)
        session.rollback()
        _release_for_retry(job_id)
    finally:
        session.close()


def fail_exhausted_feedback_summary_job(session: Session, job_id: UUID) -> None:
    """Store the template after the worker has lost this job too many times."""
    job = session.get(FeedbackSummaryJob, job_id)
    if job is None:
        return
    reason = f"Worker lost this job {job.attempts} times"
    feedback_summary.apply_summary(
        session, job, job.template_summary, FeedbackSummaryOrigin.TEMPLATE
    )
    job.status = FeedbackSummaryJobStatus.FAILED
    job.error = reason[:2000]
    session.commit()


def _release_for_retry(job_id: UUID) -> None:
    """Expire the lease so the next poll can claim the still-running job."""
    session = sync_session()
    try:
        session.execute(
            update(FeedbackSummaryJob)
            .where(
                FeedbackSummaryJob.id == job_id,
                FeedbackSummaryJob.status == FeedbackSummaryJobStatus.RUNNING,
            )
            .values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()
    except Exception:
        logger.exception("Could not release feedback summary job %s", job_id)
        session.rollback()
    finally:
        session.close()
