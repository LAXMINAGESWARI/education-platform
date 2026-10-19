"""Log assistant prompt-guard verdicts.

Revision ID: e0f1a2b3c4d5
Revises: b1c2d3e4f5a6
Create Date: 2026-10-19 21:10:00.000000

Additive table only. Existing rows are not rewritten, so this is safe to apply
on a production Postgres database that is already at b1c2d3e4f5a6.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str | Sequence[str] | None = "e0f1a2b3c4d5"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_guard_verdicts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("surface", sa.String(length=32), nullable=False),
        sa.Column("message_sha256", sa.String(length=64), nullable=False),
        sa.Column("message_preview", sa.String(length=160), nullable=False),
        sa.Column("injection_probability", sa.Float(), nullable=True),
        sa.Column("exfiltration_probability", sa.Float(), nullable=True),
        sa.Column("topic", sa.String(length=32), nullable=True),
        sa.Column("topic_probabilities", sa.JSON(), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("jev_error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('allow', 'flag', 'block', 'abuse')",
            name="ck_assistant_guard_verdicts_decision",
        ),
        sa.CheckConstraint(
            "source IN ('jev', 'heuristic', 'fail_open')",
            name="ck_assistant_guard_verdicts_source",
        ),
        sa.CheckConstraint(
            "surface IN ('policy_chat', 'text_to_sql', 'generation_review')",
            name="ck_assistant_guard_verdicts_surface",
        ),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
            name="fk_assistant_guard_verdicts_institution_id_institutions",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_assistant_guard_verdicts_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assistant_guard_verdicts"),
    )
    op.create_index(
        "ix_assistant_guard_verdicts_institution_id_created_at",
        "assistant_guard_verdicts",
        ["institution_id", "created_at"],
    )
    op.create_index(
        "ix_assistant_guard_verdicts_decision",
        "assistant_guard_verdicts",
        ["decision"],
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_guard_verdicts_decision", table_name="assistant_guard_verdicts")
    op.drop_index(
        "ix_assistant_guard_verdicts_institution_id_created_at",
        table_name="assistant_guard_verdicts",
    )
    op.drop_table("assistant_guard_verdicts")
