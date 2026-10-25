"""Admin policy chat routes (multi-conversation)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.api.deps import (
    Principal,
    require_administrator,
    require_administrator_detached,
)
from education_platform.core.errors import DomainError
from education_platform.db.session import get_session, get_session_factory
from education_platform.modules.assistant import service
from education_platform.modules.assistant.schemas import (
    ConversationDetailOut,
    ConversationSummaryOut,
    CreateConversationIn,
    CreateConversationOut,
    PostMessageIn,
    PostMessageOut,
    StreamMessageIn,
)
from education_platform.modules.assistant.stream import iter_policy_stream
from education_platform.modules.progress.sse import SSE_HEADERS

router = APIRouter(tags=["assistant"])


@router.get("/chats", response_model=list[ConversationSummaryOut])
async def list_chats(
    principal: Principal = Depends(require_administrator),
    session: AsyncSession = Depends(get_session),
) -> list[ConversationSummaryOut]:
    return await service.list_conversations(session, principal)


@router.post(
    "/chats",
    response_model=CreateConversationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat(
    body: CreateConversationIn | None = None,
    principal: Principal = Depends(require_administrator),
    session: AsyncSession = Depends(get_session),
) -> CreateConversationOut:
    title = body.title if body else None
    return await service.create_conversation(session, principal, title=title)


@router.get("/chats/{conversation_id}", response_model=ConversationDetailOut)
async def get_chat(
    conversation_id: UUID,
    principal: Principal = Depends(require_administrator),
    session: AsyncSession = Depends(get_session),
) -> ConversationDetailOut:
    return await service.get_conversation(session, principal, conversation_id)


@router.delete("/chats/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    conversation_id: UUID,
    principal: Principal = Depends(require_administrator),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await service.delete_conversation(session, principal, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chats/{conversation_id}/messages", response_model=PostMessageOut)
async def post_chat_message(
    conversation_id: UUID,
    body: PostMessageIn,
    principal: Principal = Depends(require_administrator),
    session: AsyncSession = Depends(get_session),
) -> PostMessageOut:
    return await service.post_message(
        session,
        principal,
        conversation_id,
        content=body.content,
    )


@router.post("/chats/{conversation_id}/messages/stream")
async def stream_chat_message(
    conversation_id: UUID,
    body: StreamMessageIn,
    principal: Principal = Depends(require_administrator_detached),
) -> StreamingResponse:
    """Stream checking, searching, and summarising, then the saved answer."""
    text = body.content.strip()
    if not text:
        raise DomainError("Message is empty", status_code=400)
    factory = get_session_factory()
    async with factory() as session:
        await service.owned_conversation(session, principal, conversation_id)
        await session.commit()
    return StreamingResponse(
        iter_policy_stream(
            principal=principal,
            conversation_id=conversation_id,
            content=text,
            request_id=body.request_id,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
