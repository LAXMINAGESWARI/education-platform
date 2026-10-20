"""Meter LLM gateway calls without storing prompts or responses.

Revision ID: b3c4d5e6f7a8
Revises: d6e7f8a9b0c1
Create Date: 2026-10-19 21:40:00.000000

One append-only row per gateway call. The table is new, so upgrade does not
rewrite or lock existing application tables. Prompt and response text are not
columns: metering must not retain student or policy content.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str | Sequence[str] | None = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_gateway_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feature", sa.String(length=64), nullable=False),
        sa.Column("step", sa.String(length=80), nullable=False),
        sa.Column("requested_model", sa.String(length=200), nullable=False),
        sa.Column("served_model", sa.String(length=200), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(14, 8), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_class", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'error')",
            name="ck_llm_gateway_calls_status",
        ),
        sa.CheckConstraint("prompt_tokens >= 0", name="ck_llm_gateway_calls_prompt_tokens"),
        sa.CheckConstraint(
            "completion_tokens >= 0",
            name="ck_llm_gateway_calls_completion_tokens",
        ),
        sa.CheckConstraint("cached_tokens >= 0", name="ck_llm_gateway_calls_cached_tokens"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_llm_gateway_calls_latency_ms"),
        sa.CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0",
            name="ck_llm_gateway_calls_cost_usd",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_gateway_calls")),
    )
    op.create_index(
        "ix_llm_gateway_calls_step_created_at",
        "llm_gateway_calls",
        ["step", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_llm_gateway_calls_step_created_at", table_name="llm_gateway_calls")
    op.drop_table("llm_gateway_calls")
