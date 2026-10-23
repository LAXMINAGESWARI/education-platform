"""Store streamed text-to-SQL runs so a reconnect does not execute SQL again.

Revision ID: b4c5d6e7f8a9
Revises: b3c4d5e6f7a8
Create Date: 2026-10-20 15:10:00.000000

One row per (user_id, request_id). Progress and terminal frames live in JSON.
The row expires about ten minutes after it is claimed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str | Sequence[str] | None = "b4c5d6e7f8a9"
down_revision: str | Sequence[str] | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "text_to_sql_ask_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("terminal", sa.JSON(), nullable=False),
        sa.Column("sql_executed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("audited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            "status IN ('running', 'done')",
            name="ck_text_to_sql_ask_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_text_to_sql_ask_runs_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_text_to_sql_ask_runs")),
        sa.UniqueConstraint("user_id", "request_id", name="uq_text_to_sql_ask_runs_user_request"),
    )


def downgrade() -> None:
    op.drop_table("text_to_sql_ask_runs")
