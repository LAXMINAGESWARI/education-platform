"""Move student goals from subtopics onto topics.

Revision ID: b1c2d3e4f5a6
Revises: e7f8a9b0c1d2
Create Date: 2026-10-19 18:10:00.000000

Every subtopic has a topic, so each existing goal can be placed on that
topic. When one student had several goals inside the same topic, the kept
row takes the highest target and the latest due date. Rows that cannot be
joined to a subtopic abort the migration instead of being deleted.

Downgrade reattaches each topic goal to the earliest subtopic of that
topic. It cannot restore which subtopic originally owned the goal, and it
refuses to run if a topic has no subtopic to attach to.
"""

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from education_platform.modules.assessments.goal_collapse import (
    SubtopicGoalSource,
    collapse_subtopic_goals,
)

revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("student_subtopic_goals", sa.Column("topic_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_student_subtopic_goals_topic_id_topics"),
        "student_subtopic_goals",
        "topics",
        ["topic_id"],
        ["id"],
    )
    _collapse_existing_goals()
    op.drop_constraint(
        "uq_student_subtopic_goals_student_subtopic",
        "student_subtopic_goals",
        type_="unique",
    )
    op.drop_index(
        op.f("ix_student_subtopic_goals_subtopic_id"), table_name="student_subtopic_goals"
    )
    op.drop_constraint(
        op.f("fk_student_subtopic_goals_subtopic_id_subtopics"),
        "student_subtopic_goals",
        type_="foreignkey",
    )
    op.drop_column("student_subtopic_goals", "subtopic_id")
    op.alter_column("student_subtopic_goals", "topic_id", nullable=False)
    op.create_unique_constraint(
        "uq_student_subtopic_goals_student_topic",
        "student_subtopic_goals",
        ["student_id", "topic_id"],
    )
    op.create_index(
        op.f("ix_student_subtopic_goals_topic_id"),
        "student_subtopic_goals",
        ["topic_id"],
        unique=False,
    )


def downgrade() -> None:
    op.add_column("student_subtopic_goals", sa.Column("subtopic_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE student_subtopic_goals AS goal
        SET subtopic_id = (
            SELECT subtopics.id
            FROM subtopics
            WHERE subtopics.topic_id = goal.topic_id
            ORDER BY subtopics.sequence, subtopics.id
            LIMIT 1
        )
        """
    )
    missing = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM student_subtopic_goals WHERE subtopic_id IS NULL"))
        .scalar_one()
    )
    if missing:
        raise RuntimeError(
            "Cannot restore subtopic goals: a topic goal has no subtopic to attach to"
        )
    op.drop_index(op.f("ix_student_subtopic_goals_topic_id"), table_name="student_subtopic_goals")
    op.drop_constraint(
        "uq_student_subtopic_goals_student_topic",
        "student_subtopic_goals",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_student_subtopic_goals_topic_id_topics"),
        "student_subtopic_goals",
        type_="foreignkey",
    )
    op.drop_column("student_subtopic_goals", "topic_id")
    op.alter_column("student_subtopic_goals", "subtopic_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_student_subtopic_goals_subtopic_id_subtopics"),
        "student_subtopic_goals",
        "subtopics",
        ["subtopic_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_student_subtopic_goals_student_subtopic",
        "student_subtopic_goals",
        ["student_id", "subtopic_id"],
    )
    op.create_index(
        op.f("ix_student_subtopic_goals_subtopic_id"),
        "student_subtopic_goals",
        ["subtopic_id"],
        unique=False,
    )


def _collapse_existing_goals() -> None:
    bind = op.get_bind()
    unmapped = bind.execute(
        sa.text(
            """
            SELECT count(*)
            FROM student_subtopic_goals AS goal
            LEFT JOIN subtopics ON subtopics.id = goal.subtopic_id
            WHERE subtopics.id IS NULL
            """
        )
    ).scalar_one()
    if unmapped:
        raise RuntimeError(
            f"{unmapped} student goals have no subtopic; left them in place and stopped"
        )
    selected = bind.execute(
        sa.text(
            """
            SELECT goal.id, goal.student_id, subtopics.topic_id,
                   goal.target_percent, goal.due_at
            FROM student_subtopic_goals AS goal
            JOIN subtopics ON subtopics.id = goal.subtopic_id
            """
        )
    ).all()
    sources = [
        SubtopicGoalSource(
            id=row.id,
            student_id=row.student_id,
            topic_id=row.topic_id,
            target_percent=Decimal(row.target_percent),
            due_at=row.due_at,
        )
        for row in selected
    ]
    for collapsed in collapse_subtopic_goals(sources):
        bind.execute(
            sa.text(
                """
                UPDATE student_subtopic_goals
                SET topic_id = :topic_id,
                    target_percent = :target_percent,
                    due_at = :due_at
                WHERE id = :id
                """
            ),
            {
                "id": collapsed.keep_id,
                "topic_id": collapsed.topic_id,
                "target_percent": collapsed.target_percent,
                "due_at": collapsed.due_at,
            },
        )
        for drop_id in collapsed.drop_ids:
            bind.execute(
                sa.text("DELETE FROM student_subtopic_goals WHERE id = :id"),
                {"id": drop_id},
            )
    leftover = bind.execute(
        sa.text("SELECT count(*) FROM student_subtopic_goals WHERE topic_id IS NULL")
    ).scalar_one()
    if leftover:
        raise RuntimeError(f"{leftover} student goals still have no topic after collapse")
