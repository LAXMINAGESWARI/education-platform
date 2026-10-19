"""Small quiz loaders shared by assessments, materials, and academics.

These are query helpers, not the attempts service — callers may import them without
creating a service-to-service cycle.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    QuestionOption,
    QuestionVersion,
    QuizAttempt,
    QuizAttemptStatus,
    QuizItem,
    QuizRelease,
    QuizReleaseStatus,
    QuizScope,
    QuizVersion,
    QuizVersionStatus,
)
from education_platform.modules.assessments.schemas import (
    AttemptHistoryItem,
    QuizOption,
    QuizQuestion,
)

# Statuses whose score/pass may credit unlock gates and analytics. HELD means
# admin_release scored the attempt but results are not student-visible yet.
PASS_CREDIT_STATUSES = frozenset(
    {
        QuizAttemptStatus.SUBMITTED,
        QuizAttemptStatus.SCORED,
        QuizAttemptStatus.RELEASED,
    }
)


def results_visible(attempt: QuizAttempt) -> bool:
    """Whether score/pass may be shown to the student for this attempt."""
    return attempt.status != QuizAttemptStatus.HELD


def history_item_for(attempt: QuizAttempt) -> AttemptHistoryItem:
    visible = results_visible(attempt)
    return AttemptHistoryItem(
        id=attempt.id,
        attempt_number=attempt.attempt_number,
        status=attempt.status.value,
        score_percent=attempt.score_percent if visible else None,
        passed=attempt.passed if visible else None,
        submitted_at=attempt.submitted_at,
        started_at=attempt.started_at,
    )


async def released_quiz(
    session: AsyncSession,
    *,
    quiz_scope: QuizScope,
    target_id: UUID,
) -> tuple[CommonMasteryQuiz, QuizVersion] | None:
    target_filter = (
        CommonMasteryQuiz.subtopic_id == target_id
        if quiz_scope == QuizScope.SUBTOPIC_MASTERY
        else CommonMasteryQuiz.topic_id == target_id
    )
    row = (
        await session.execute(
            select(CommonMasteryQuiz, QuizVersion)
            .join(QuizVersion, QuizVersion.quiz_id == CommonMasteryQuiz.id)
            .where(
                CommonMasteryQuiz.quiz_scope == quiz_scope,
                target_filter,
                QuizVersion.lifecycle_status == QuizVersionStatus.RELEASED,
            )
            .order_by(QuizVersion.version_number.desc())
        )
    ).first()
    return (row[0], row[1]) if row is not None else None


async def questions_for_numbered(
    session: AsyncSession, numbered: Sequence[tuple[int, UUID]]
) -> list[QuizQuestion]:
    """Student-facing questions for (number, question_version_id) pairs.

    Does not join answer keys. `number` is whatever the caller served (bank
    sequence, or 1..n on a draw), not looked up again from `quiz_items`.
    """
    questions: list[QuizQuestion] = []
    for number, question_version_id in numbered:
        version = await session.scalar(
            select(QuestionVersion).where(QuestionVersion.id == question_version_id)
        )
        if version is None:
            continue
        options = (
            await session.scalars(
                select(QuestionOption)
                .where(QuestionOption.question_version_id == version.id)
                .order_by(QuestionOption.sequence, QuestionOption.label)
            )
        ).all()
        questions.append(
            QuizQuestion(
                number=number,
                difficulty=version.difficulty.value.title() if version.difficulty else None,
                prompt=version.prompt,
                options=[QuizOption(label=option.label, text=option.text) for option in options],
            )
        )
    return questions


async def questions_for_quiz_version(
    session: AsyncSession, quiz_version_id: UUID
) -> list[QuizQuestion]:
    """Every item on a quiz version, in bank order. Teacher and admin previews use this."""
    items = (
        await session.scalars(
            select(QuizItem)
            .where(QuizItem.quiz_version_id == quiz_version_id)
            .order_by(QuizItem.sequence)
        )
    ).all()
    return await questions_for_numbered(
        session,
        [(item.sequence, item.question_version_id) for item in items],
    )


async def open_release_for_quiz_version(
    session: AsyncSession, quiz_version_id: UUID
) -> QuizRelease | None:
    now = datetime.now(UTC)
    return cast(
        QuizRelease | None,
        await session.scalar(
            select(QuizRelease).where(
                QuizRelease.quiz_version_id == quiz_version_id,
                QuizRelease.status == QuizReleaseStatus.OPEN,
                (QuizRelease.window_starts_at.is_(None) | (QuizRelease.window_starts_at <= now)),
                (QuizRelease.window_ends_at.is_(None) | (QuizRelease.window_ends_at >= now)),
            )
        ),
    )
