"""Student-set targets for a topic quiz.

Status (on track / achieved / missed) is computed live from the topic quiz's
best visible score on every read. A stored status would need a background job
to stay in sync as new attempts come in.

The table is still ``student_subtopic_goals``. Goals used to hang off a
subtopic; they now hang off the topic, one per student.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.core.errors import DomainError
from education_platform.modules.academics.service import load_topic_node, require_subject_enrollment
from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    QuizAttempt,
    QuizAttemptStatus,
    QuizScope,
    QuizVersion,
    StudentSubtopicGoal,
)
from education_platform.modules.assessments.schemas import GoalOut, SetGoalRequest
from education_platform.modules.authorization.scope import Scope


def compute_goal_status(
    current_percent: Decimal | None,
    target_percent: Decimal,
    due_at: datetime,
    now: datetime,
) -> Literal["on_track", "achieved", "missed"]:
    if current_percent is not None and current_percent >= target_percent:
        return "achieved"
    if now > due_at:
        return "missed"
    return "on_track"


async def goal_for_topic(
    session: AsyncSession, student_id: UUID, topic_id: UUID
) -> StudentSubtopicGoal | None:
    return cast(
        "StudentSubtopicGoal | None",
        await session.scalar(
            select(StudentSubtopicGoal).where(
                StudentSubtopicGoal.student_id == student_id,
                StudentSubtopicGoal.topic_id == topic_id,
            )
        ),
    )


async def goals_for_topics(
    session: AsyncSession, student_id: UUID, topic_ids: list[UUID]
) -> dict[UUID, StudentSubtopicGoal]:
    """One lookup for every topic on a feedback page."""
    if not topic_ids:
        return {}
    rows = await session.scalars(
        select(StudentSubtopicGoal).where(
            StudentSubtopicGoal.student_id == student_id,
            StudentSubtopicGoal.topic_id.in_(topic_ids),
        )
    )
    return {row.topic_id: row for row in rows}


async def topic_best_percents(
    session: AsyncSession, student_id: UUID, topic_ids: list[UUID]
) -> dict[UUID, Decimal]:
    """Best visible topic-quiz score. Abandoned, held, and unscored attempts are out."""
    if not topic_ids:
        return {}
    rows = (
        await session.execute(
            select(CommonMasteryQuiz.topic_id, func.max(QuizAttempt.score_percent))
            .join(QuizVersion, QuizVersion.id == QuizAttempt.quiz_version_id)
            .join(CommonMasteryQuiz, CommonMasteryQuiz.id == QuizVersion.quiz_id)
            .where(
                QuizAttempt.student_id == student_id,
                CommonMasteryQuiz.quiz_scope == QuizScope.TOPIC_MASTERY,
                CommonMasteryQuiz.topic_id.in_(topic_ids),
                QuizAttempt.status != QuizAttemptStatus.ABANDONED,
                QuizAttempt.status != QuizAttemptStatus.HELD,
                QuizAttempt.score_percent.is_not(None),
            )
            .group_by(CommonMasteryQuiz.topic_id)
        )
    ).all()
    return {
        topic_id: Decimal(score)
        for topic_id, score in rows
        if topic_id is not None and score is not None
    }


def goal_out(
    goal: StudentSubtopicGoal, topic_name: str, current_percent: Decimal | None
) -> GoalOut:
    return GoalOut(
        topic_id=goal.topic_id,
        topic_name=topic_name,
        target_percent=goal.target_percent,
        due_at=goal.due_at,
        current_percent=current_percent,
        status=compute_goal_status(
            current_percent, goal.target_percent, goal.due_at, datetime.now(UTC)
        ),
    )


async def _authorized_topic(session: AsyncSession, scope: Scope, topic_id: UUID) -> str:
    """Confirm the scoped student is enrolled in this topic's subject; return its name."""
    node = await load_topic_node(session, topic_id)
    if node is None:
        raise DomainError("Topic not found", status_code=404)
    if not scope.covers_offering(node.offering.id, institution_id=node.institution.id):
        raise DomainError("Topic not found", status_code=404)
    await require_subject_enrollment(session, scope, node.offering.id)
    return node.topic.name


async def set_goal(
    session: AsyncSession, scope: Scope, topic_id: UUID, payload: SetGoalRequest
) -> GoalOut:
    if scope.self_student_id is None:
        raise DomainError("Student enrollment required", status_code=403)
    student_id = scope.self_student_id
    topic_name = await _authorized_topic(session, scope, topic_id)

    if payload.due_at <= datetime.now(UTC):
        raise DomainError("Goal due date must be in the future", status_code=422)

    goal = await goal_for_topic(session, student_id, topic_id)
    if goal is None:
        goal = StudentSubtopicGoal(
            student_id=student_id,
            topic_id=topic_id,
            target_percent=payload.target_percent,
            due_at=payload.due_at,
        )
        session.add(goal)
    else:
        goal.target_percent = payload.target_percent
        goal.due_at = payload.due_at
    await session.flush()
    await session.refresh(goal)

    best = await topic_best_percents(session, student_id, [topic_id])
    return goal_out(goal, topic_name, best.get(topic_id))


async def delete_goal(session: AsyncSession, scope: Scope, topic_id: UUID) -> None:
    if scope.self_student_id is None:
        raise DomainError("Student enrollment required", status_code=403)
    student_id = scope.self_student_id
    await _authorized_topic(session, scope, topic_id)

    goal = await goal_for_topic(session, student_id, topic_id)
    if goal is None:
        raise DomainError("Goal not found", status_code=404)
    await session.delete(goal)
    await session.flush()
