"""Queue feedback summaries for the worker.

Revision ID: f4a5b6c7d8e9
Revises: c1e2f3a4b5d6
Create Date: 2026-10-18 07:20:00.000000

The student feedback page reads `summary_origin` to tell model prose from the
template. Generation itself is a `feedback_summary_jobs` row claimed by the
existing worker.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str | Sequence[str] | None = "f4a5b6c7d8e9"
down_revision: str | Sequence[str] | None = "c1e2f3a4b5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subject_feedback_summaries",
        sa.Column("summary_origin", sa.String(length=16), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE subject_feedback_summaries "
            "SET summary_origin = 'model' "
            "WHERE summary IS NOT NULL AND facts_hash IS NOT NULL"
        )
    )
    op.create_table(
        "feedback_summary_jobs",
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("facts_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("template_summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_feedback_summary_jobs_student_id_student_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_feedback_summary_jobs_subject_id_subjects"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feedback_summary_jobs")),
    )
    op.create_index(
        op.f("ix_feedback_summary_jobs_student_id"),
        "feedback_summary_jobs",
        ["student_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feedback_summary_jobs_subject_id"),
        "feedback_summary_jobs",
        ["subject_id"],
        unique=False,
    )
    op.create_index(
        "ix_feedback_summary_jobs_status_created_at",
        "feedback_summary_jobs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_feedback_summary_jobs_one_in_flight",
        "feedback_summary_jobs",
        ["student_id", "subject_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_feedback_summary_jobs_one_in_flight",
        table_name="feedback_summary_jobs",
    )
    op.drop_index(
        "ix_feedback_summary_jobs_status_created_at",
        table_name="feedback_summary_jobs",
    )
    op.drop_index(
        op.f("ix_feedback_summary_jobs_subject_id"),
        table_name="feedback_summary_jobs",
    )
    op.drop_index(
        op.f("ix_feedback_summary_jobs_student_id"),
        table_name="feedback_summary_jobs",
    )
    op.drop_table("feedback_summary_jobs")
    op.drop_column("subject_feedback_summaries", "summary_origin")
