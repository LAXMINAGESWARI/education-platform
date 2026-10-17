"""Student-set targets for a subtopic (§8 goal-setting).

Status (on track / achieved / missed) is computed live from current feedback data on every
read, not stored — storing it would need a background job to stay in sync as new attempts
come in, and a subtopic's percent can already be read cheaply via feedback_service's own
aggregation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.core.errors import DomainError
from education_platform.modules.academics.service import (
    load_subtopic_node,
    require_subject_enrollment,
)
from education_platform.modules.assessments import feedback_service
from education_platform.modules.assessments.models import (
    QuizAttempt,
    QuizAttemptStatus,
    StudentSubtopicGoal,
)
from education_platform.modules.assessments.schemas import GoalOut, SetGoalRequest
from education_platform.modules.authorization.scope import Scope

_RELEASED_STATUSES = (QuizAttemptStatus.SCORED, QuizAttemptStatus.RELEASED)


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


async def goal_for_subtopic(
    session: AsyncSession, student_id: UUID, subtopic_id: UUID
) -> StudentSubtopicGoal | None:
    return cast(
        "StudentSubtopicGoal | None",
        await session.scalar(
            select(StudentSubtopicGoal).where(
                StudentSubtopicGoal.student_id == student_id,
                StudentSubtopicGoal.subtopic_id == subtopic_id,
            )
        ),
    )


async def current_percent_for_subtopic(
    session: AsyncSession, student_id: UUID, subtopic_id: UUID
) -> Decimal | None:
    attempt_ids = list(
        await session.scalars(
            select(QuizAttempt.id).where(
                QuizAttempt.student_id == student_id,
                QuizAttempt.status.in_(_RELEASED_STATUSES),
            )
        )
    )
    rows = await feedback_service.answer_rows_for_attempts(session, attempt_ids)
    performance = feedback_service.aggregate_by_subtopic(rows)
    perf = performance.get(subtopic_id)
    return perf.percent if perf is not None else None


def goal_out(
    goal: StudentSubtopicGoal, subtopic_name: str, current_percent: Decimal | None
) -> GoalOut:
    """Build the response shape for a goal row — also used by feedback_service to attach a
    subtopic's goal onto its dashboard entry (deferred-imported there to avoid a cycle, since
    this module imports feedback_service for its own percent calculation)."""
    return GoalOut(
        subtopic_id=goal.subtopic_id,
        subtopic_name=subtopic_name,
        target_percent=goal.target_percent,
        due_at=goal.due_at,
        current_percent=current_percent,
        status=compute_goal_status(
            current_percent, goal.target_percent, goal.due_at, datetime.now(UTC)
        ),
    )


async def _authorized_subtopic(session: AsyncSession, scope: Scope, subtopic_id: UUID) -> str:
    """Confirm the scoped student is enrolled in this subtopic's subject; return its name."""
    node = await load_subtopic_node(session, subtopic_id)
    if node is None or node.subtopic is None:
        raise DomainError("Subtopic not found", status_code=404)
    if not scope.covers_offering(node.offering.id, institution_id=node.institution.id):
        raise DomainError("Subtopic not found", status_code=404)
    await require_subject_enrollment(session, scope, node.offering.id)
    return node.subtopic.name


async def set_goal(
    session: AsyncSession, scope: Scope, subtopic_id: UUID, payload: SetGoalRequest
) -> GoalOut:
    if scope.self_student_id is None:
        raise DomainError("Student enrollment required", status_code=403)
    student_id = scope.self_student_id
    subtopic_name = await _authorized_subtopic(session, scope, subtopic_id)

    if payload.due_at <= datetime.now(UTC):
        raise DomainError("Goal due date must be in the future", status_code=422)

    goal = await goal_for_subtopic(session, student_id, subtopic_id)
    if goal is None:
        goal = StudentSubtopicGoal(
            student_id=student_id,
            subtopic_id=subtopic_id,
            target_percent=payload.target_percent,
            due_at=payload.due_at,
        )
        session.add(goal)
    else:
        goal.target_percent = payload.target_percent
        goal.due_at = payload.due_at
    await session.flush()
    await session.refresh(goal)

    current_percent = await current_percent_for_subtopic(session, student_id, subtopic_id)
    return goal_out(goal, subtopic_name, current_percent)


async def delete_goal(session: AsyncSession, scope: Scope, subtopic_id: UUID) -> None:
    if scope.self_student_id is None:
        raise DomainError("Student enrollment required", status_code=403)
    student_id = scope.self_student_id
    await _authorized_subtopic(session, scope, subtopic_id)

    goal = await goal_for_subtopic(session, student_id, subtopic_id)
    if goal is None:
        raise DomainError("Goal not found", status_code=404)
    await session.delete(goal)
    await session.flush()
