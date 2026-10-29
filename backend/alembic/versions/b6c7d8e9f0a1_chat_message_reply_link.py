"""Link a policy reply to the question it answers.

Revision ID: b6c7d8e9f0a1
Revises: c0d1e2f3a4b5
Create Date: 2026-10-29 09:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str | Sequence[str] | None = "b6c7d8e9f0a1"
down_revision: str | Sequence[str] | None = "c0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("reply_to_message_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_chat_messages_reply_to_message_id_chat_messages"),
        "chat_messages",
        "chat_messages",
        ["reply_to_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_chat_messages_reply_to_message_id"),
        "chat_messages",
        ["reply_to_message_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_messages_reply_to_message_id"), table_name="chat_messages")
    op.drop_constraint(
        op.f("fk_chat_messages_reply_to_message_id_chat_messages"),
        "chat_messages",
        type_="foreignkey",
    )
    op.drop_column("chat_messages", "reply_to_message_id")
