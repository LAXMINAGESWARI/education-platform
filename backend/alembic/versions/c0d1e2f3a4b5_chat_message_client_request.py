"""client request id on policy chat messages

Revision ID: c0d1e2f3a4b5
Revises: e9f0a1b2c3d4
Create Date: 2026-10-25 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str | Sequence[str] | None = "c0d1e2f3a4b5"
down_revision: str | Sequence[str] | None = "e9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("client_request_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "uq_chat_messages_conversation_client_request",
        "chat_messages",
        ["conversation_id", "client_request_id"],
        unique=True,
        postgresql_where=sa.text("client_request_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_chat_messages_conversation_client_request",
        table_name="chat_messages",
    )
    op.drop_column("chat_messages", "client_request_id")
