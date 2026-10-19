"""Collapse legacy per-subtopic goals into one goal per topic.

Each subtopic belongs to exactly one topic, so the mapping is reliable.
For one student and topic, the kept goal uses the highest target and the
latest due date, even when those values lived on different subtopic rows.
The Alembic migration and the unit tests share this function.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SubtopicGoalSource:
    id: UUID
    student_id: UUID
    topic_id: UUID
    target_percent: Decimal
    due_at: datetime


@dataclass(frozen=True, slots=True)
class CollapsedTopicGoal:
    keep_id: UUID
    student_id: UUID
    topic_id: UUID
    target_percent: Decimal
    due_at: datetime
    drop_ids: tuple[UUID, ...]


def collapse_subtopic_goals(rows: list[SubtopicGoalSource]) -> list[CollapsedTopicGoal]:
    """One goal per (student, topic). Other rows are listed for deletion."""
    grouped: dict[tuple[UUID, UUID], list[SubtopicGoalSource]] = {}
    for row in rows:
        grouped.setdefault((row.student_id, row.topic_id), []).append(row)
    collapsed: list[CollapsedTopicGoal] = []
    for (student_id, topic_id), group in grouped.items():
        keeper = min(group, key=lambda row: row.id)
        collapsed.append(
            CollapsedTopicGoal(
                keep_id=keeper.id,
                student_id=student_id,
                topic_id=topic_id,
                target_percent=max(row.target_percent for row in group),
                due_at=max(row.due_at for row in group),
                drop_ids=tuple(row.id for row in group if row.id != keeper.id),
            )
        )
    return collapsed
