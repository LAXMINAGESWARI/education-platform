"""API schemas for multi-conversation policy chat."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ChatCitationOut(BaseModel):
    id: str
    label: str
    excerpt: str


class ChatMessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    citations: list[ChatCitationOut] | None = None
    created_at: datetime


class ContextUsageOut(BaseModel):
    used_tokens: int
    limit_tokens: int
    used_percent: int


class ConversationSummaryOut(BaseModel):
    id: UUID
    title: str
    updated_at: datetime
    context: ContextUsageOut


class ConversationDetailOut(BaseModel):
    id: UUID
    title: str
    updated_at: datetime
    context: ContextUsageOut
    messages: list[ChatMessageOut]


class CreateConversationIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class CreateConversationOut(ConversationSummaryOut):
    pass


class PostMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class StreamMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    request_id: UUID


class PostMessageOut(BaseModel):
    user_message: ChatMessageOut
    assistant_message: ChatMessageOut
    context: ContextUsageOut
