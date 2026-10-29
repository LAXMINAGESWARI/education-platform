"""Persisted multi-conversation chat models for the policy assistant."""

from __future__ import annotations

import enum
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from education_platform.db.base import Base, UUIDTimestampMixin
from education_platform.db.types import JsonDict, str_enum


class ChatMessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ChatConversation(UUIDTimestampMixin, Base):
    __tablename__ = "chat_conversations"

    institution_id: Mapped[UUID] = mapped_column(ForeignKey("institutions.id"), index=True)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New chat")
    context_used_tokens: Mapped[int] = mapped_column(Integer, default=0)
    context_limit_tokens: Mapped[int] = mapped_column(Integer, default=20_000)
    context_used_percent: Mapped[int] = mapped_column(Integer, default=0)


class ChatMessage(UUIDTimestampMixin, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index(
            "uq_chat_messages_conversation_client_request",
            "conversation_id",
            "client_request_id",
            unique=True,
            postgresql_where=text("client_request_id IS NOT NULL"),
        ),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[ChatMessageRole] = mapped_column(str_enum(ChatMessageRole, "chat_message_role"))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[Any] | None] = mapped_column(JsonDict, nullable=True)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    client_request_id: Mapped[UUID | None] = mapped_column(nullable=True)
    reply_to_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
