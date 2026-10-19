"""Per-attempt question draws.

Revision ID: b7c8d9e0f1a2
Revises: f4a5b6c7d8e9
Create Date: 2026-10-19 07:30:00.000000

`quiz_versions.attempt_question_count` is NULL when a start should serve the
whole bank. Generated topic-mastery versions are backfilled to 10. Each new
attempt records the question versions it actually served in `attempt_questions`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str | Sequence[str] | None = "b7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "f4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quiz_versions",
        sa.Column("attempt_question_count", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_quiz_versions_attempt_question_count",
        "quiz_versions",
        "attempt_question_count IS NULL OR attempt_question_count > 0",
    )
    op.execute(
        sa.text(
            """
            UPDATE quiz_versions AS qv
            SET attempt_question_count = 10
            WHERE qv.id IN (
                SELECT draft_quiz_version_id
                FROM content_generation_runs
                WHERE draft_quiz_version_id IS NOT NULL
                UNION
                SELECT published_quiz_version_id
                FROM content_generation_runs
                WHERE published_quiz_version_id IS NOT NULL
            )
            """
        )
    )
    op.create_table(
        "attempt_questions",
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("question_version_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
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
        sa.CheckConstraint("position >= 1", name="ck_attempt_questions_position"),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["quiz_attempts.id"],
            name=op.f("fk_attempt_questions_attempt_id_quiz_attempts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"],
            ["question_versions.id"],
            name=op.f("fk_attempt_questions_question_version_id_question_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attempt_questions")),
        sa.UniqueConstraint(
            "attempt_id",
            "position",
            name="uq_attempt_questions_attempt_position",
        ),
        sa.UniqueConstraint(
            "attempt_id",
            "question_version_id",
            name="uq_attempt_questions_attempt_question",
        ),
    )
    op.create_index(
        op.f("ix_attempt_questions_attempt_id"),
        "attempt_questions",
        ["attempt_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_attempt_questions_question_version_id"),
        "attempt_questions",
        ["question_version_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_attempt_questions_question_version_id"), table_name="attempt_questions")
    op.drop_index(op.f("ix_attempt_questions_attempt_id"), table_name="attempt_questions")
    op.drop_table("attempt_questions")
    op.drop_constraint("ck_quiz_versions_attempt_question_count", "quiz_versions", type_="check")
    op.drop_column("quiz_versions", "attempt_question_count")
