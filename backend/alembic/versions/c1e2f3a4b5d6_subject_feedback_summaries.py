"""cache subject feedback summaries

Revision ID: c1e2f3a4b5d6
Revises: 5ed73ef83711
Create Date: 2026-10-18 06:10:00.000000

Stores generated feedback prose so the student feedback request does not call
OpenRouter. Hand-written for this table only.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str | Sequence[str] | None = "c1e2f3a4b5d6"
down_revision: str | Sequence[str] | None = "5ed73ef83711"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subject_feedback_summaries",
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("facts_hash", sa.String(length=64), nullable=True),
        sa.Column("requested_hash", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["student_profiles.id"],
            name=op.f("fk_subject_feedback_summaries_student_id_student_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_subject_feedback_summaries_subject_id_subjects"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subject_feedback_summaries")),
        sa.UniqueConstraint(
            "student_id",
            "subject_id",
            name="uq_subject_feedback_summaries_student_subject",
        ),
    )
    op.create_index(
        op.f("ix_subject_feedback_summaries_student_id"),
        "subject_feedback_summaries",
        ["student_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_subject_feedback_summaries_subject_id"),
        "subject_feedback_summaries",
        ["subject_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_subject_feedback_summaries_subject_id"),
        table_name="subject_feedback_summaries",
    )
    op.drop_index(
        op.f("ix_subject_feedback_summaries_student_id"),
        table_name="subject_feedback_summaries",
    )
    op.drop_table("subject_feedback_summaries")
