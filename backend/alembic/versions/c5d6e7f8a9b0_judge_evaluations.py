"""Store shadow Jev judgments for lessons and quiz items.

Revision ID: c5d6e7f8a9b0
Revises: b1c2d3e4f5a6
Create Date: 2026-10-19 20:40:00.000000

Rows are advisory. The generation gate keeps its own verdict. Deleting a run
removes its judgments. Deleting a question version or job leaves the row and
clears that foreign key.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str | Sequence[str] | None = "c5d6e7f8a9b0"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "judge_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("generation_job_id", sa.Uuid(), nullable=True),
        sa.Column("question_version_id", sa.Uuid(), nullable=True),
        sa.Column("subject_kind", sa.String(length=32), nullable=False),
        sa.Column("subject_key", sa.Uuid(), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 8), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("blind_status", sa.String(length=16), nullable=True),
        sa.Column("blind_answer", sa.String(length=8), nullable=True),
        sa.Column("gate_verdict", sa.String(length=32), nullable=True),
        sa.Column("gate_servable", sa.Boolean(), nullable=True),
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
        sa.CheckConstraint(
            "subject_kind IN ('lesson_section', 'lesson', 'quiz_item')",
            name="ck_judge_evaluations_subject_kind",
        ),
        sa.CheckConstraint(
            "mode IN ('off', 'shadow', 'augment', 'replace')",
            name="ck_judge_evaluations_mode",
        ),
        sa.CheckConstraint(
            "decision IN ('accept', 'needs_review', 'reject', 'error')",
            name="ck_judge_evaluations_decision",
        ),
        sa.CheckConstraint("cost_usd >= 0", name="ck_judge_evaluations_cost_usd"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_judge_evaluations_latency_ms"),
        sa.CheckConstraint("input_tokens >= 0", name="ck_judge_evaluations_input_tokens"),
        sa.CheckConstraint("output_tokens >= 0", name="ck_judge_evaluations_output_tokens"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["content_generation_runs.id"],
            name="fk_judge_evaluations_run_id_content_generation_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["generation_job_id"],
            ["generation_jobs.id"],
            name="fk_judge_evaluations_generation_job_id_generation_jobs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"],
            ["question_versions.id"],
            name="fk_judge_evaluations_question_version_id_question_versions",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_judge_evaluations"),
    )
    op.create_index(
        "ix_judge_evaluations_run_id",
        "judge_evaluations",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_judge_evaluations_question_version_id",
        "judge_evaluations",
        ["question_version_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_judge_evaluations_question_version_id", table_name="judge_evaluations")
    op.drop_index("ix_judge_evaluations_run_id", table_name="judge_evaluations")
    op.drop_table("judge_evaluations")
