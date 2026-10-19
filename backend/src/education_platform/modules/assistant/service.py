"""Conversation CRUD and LangGraph turn orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.core.config import get_settings
from education_platform.core.errors import DomainError
from education_platform.modules.assistant.models import (
    ChatConversation,
    ChatMessage,
    ChatMessageRole,
)
from education_platform.modules.assistant.schemas import (
    ChatCitationOut,
    ChatMessageOut,
    ContextUsageOut,
    ConversationDetailOut,
    ConversationSummaryOut,
    CreateConversationOut,
    PostMessageOut,
)
from education_platform.modules.assistant.strategy import (
    AssistantKind,
    ChatTurn,
    PolicyAssistantContext,
    graph_for,
)
from education_platform.modules.assistant.tokens import context_percent, estimate_tokens
from education_platform.modules.authorization.principal import Principal


def _context_out(conv: ChatConversation) -> ContextUsageOut:
    settings = get_settings()
    limit = settings.chat_context_limit_tokens
    used = conv.context_used_tokens
    return ContextUsageOut(
        used_tokens=used,
        limit_tokens=limit,
        used_percent=context_percent(used, limit),
    )


def _message_out(msg: ChatMessage) -> ChatMessageOut:
    citations: list[ChatCitationOut] | None = None
    if msg.citations:
        citations = [
            ChatCitationOut(
                id=str(item.get("id", "")),
                label=str(item.get("label", "")),
                excerpt=str(item.get("excerpt", "")),
            )
            for item in msg.citations
            if isinstance(item, dict)
        ]
    return ChatMessageOut(
        id=msg.id,
        role=msg.role.value,
        content=msg.content,
        citations=citations,
        created_at=msg.created_at,
    )


def _summary_out(conv: ChatConversation) -> ConversationSummaryOut:
    return ConversationSummaryOut(
        id=conv.id,
        title=conv.title,
        updated_at=conv.updated_at,
        context=_context_out(conv),
    )


async def _get_owned_conversation(
    session: AsyncSession,
    principal: Principal,
    conversation_id: UUID,
) -> ChatConversation:
    conv = await session.get(ChatConversation, conversation_id)
    if (
        conv is None
        or conv.owner_user_id != principal.user_id
        or conv.institution_id != principal.institution_id
    ):
        raise DomainError("Conversation not found", status_code=404)
    return conv


async def list_conversations(
    session: AsyncSession,
    principal: Principal,
) -> list[ConversationSummaryOut]:
    rows = (
        await session.scalars(
            select(ChatConversation)
            .where(
                ChatConversation.owner_user_id == principal.user_id,
                ChatConversation.institution_id == principal.institution_id,
            )
            .order_by(ChatConversation.updated_at.desc())
        )
    ).all()
    return [_summary_out(row) for row in rows]


async def create_conversation(
    session: AsyncSession,
    principal: Principal,
    *,
    title: str | None,
) -> CreateConversationOut:
    settings = get_settings()
    conv = ChatConversation(
        institution_id=principal.institution_id,
        owner_user_id=principal.user_id,
        title=(title or "New chat").strip()[:200] or "New chat",
        context_used_tokens=0,
        context_limit_tokens=settings.chat_context_limit_tokens,
        context_used_percent=0,
    )
    session.add(conv)
    await session.flush()
    session.add(
        ChatMessage(
            conversation_id=conv.id,
            role=ChatMessageRole.ASSISTANT,
            content=(
                "Ask about attendance, assessments, enrollment, or other indexed policy documents. "
                "Answers are grounded in retrieved institution sources when available."
            ),
            citations=None,
            token_estimate=0,
        )
    )
    await session.flush()
    await session.refresh(conv)
    return CreateConversationOut(
        id=conv.id,
        title=conv.title,
        updated_at=conv.updated_at,
        context=_context_out(conv),
    )


async def get_conversation(
    session: AsyncSession,
    principal: Principal,
    conversation_id: UUID,
) -> ConversationDetailOut:
    conv = await _get_owned_conversation(session, principal, conversation_id)
    messages = (
        await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conv.id)
            .order_by(ChatMessage.created_at.asc())
        )
    ).all()
    return ConversationDetailOut(
        id=conv.id,
        title=conv.title,
        updated_at=conv.updated_at,
        context=_context_out(conv),
        messages=[_message_out(m) for m in messages],
    )


async def delete_conversation(
    session: AsyncSession,
    principal: Principal,
    conversation_id: UUID,
) -> None:
    conv = await _get_owned_conversation(session, principal, conversation_id)
    await session.delete(conv)
    await session.flush()


def _trim_history_for_context(
    history: list[dict[str, str]],
    *,
    limit_tokens: int,
    reserve: int,
) -> list[dict[str, str]]:
    budget = max(0, limit_tokens - reserve)
    selected: list[dict[str, str]] = []
    used = 0
    for turn in reversed(history):
        cost = estimate_tokens(turn.get("content", ""))
        if used + cost > budget and selected:
            break
        selected.append(turn)
        used += cost
    selected.reverse()
    return selected


async def post_message(
    session: AsyncSession,
    principal: Principal,
    conversation_id: UUID,
    *,
    content: str,
) -> PostMessageOut:
    settings = get_settings()
    conv = await _get_owned_conversation(session, principal, conversation_id)
    text = content.strip()
    if not text:
        raise DomainError("Message is empty", status_code=400)

    prior = (
        await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conv.id)
            .order_by(ChatMessage.created_at.asc())
        )
    ).all()
    history = [
        {"role": m.role.value, "content": m.content}
        for m in prior
        if m.role in {ChatMessageRole.USER, ChatMessageRole.ASSISTANT}
    ]
    trimmed = _trim_history_for_context(
        history,
        limit_tokens=settings.chat_context_limit_tokens,
        reserve=estimate_tokens(text) + 800,
    )
    history_turns: list[ChatTurn] = []
    for turn in trimmed:
        role = turn.get("role")
        content = turn.get("content", "")
        if role == "user":
            history_turns.append(ChatTurn(role="user", content=content))
        elif role == "assistant":
            history_turns.append(ChatTurn(role="assistant", content=content))

    user_msg = ChatMessage(
        conversation_id=conv.id,
        role=ChatMessageRole.USER,
        content=text,
        citations=None,
        token_estimate=estimate_tokens(text),
    )
    session.add(user_msg)
    await session.flush()

    reply = await graph_for(AssistantKind.POLICY).run_turn(
        session=session,
        principal=principal,
        context=PolicyAssistantContext(kind="policy", institution_id=principal.institution_id),
        history=tuple(history_turns),
        message=text,
    )
    assistant_text = reply.content
    citations_raw = [
        {"id": item.id, "label": item.label, "excerpt": item.excerpt} for item in reply.citations
    ]
    prompt_tokens = estimate_tokens(text) + sum(
        estimate_tokens(turn.content) for turn in history_turns
    )

    assistant_msg = ChatMessage(
        conversation_id=conv.id,
        role=ChatMessageRole.ASSISTANT,
        content=assistant_text,
        citations=list(citations_raw) if citations_raw else None,
        token_estimate=estimate_tokens(assistant_text),
    )
    session.add(assistant_msg)

    used = prompt_tokens
    conv.context_used_tokens = used
    conv.context_limit_tokens = settings.chat_context_limit_tokens
    conv.context_used_percent = context_percent(used, settings.chat_context_limit_tokens)
    conv.updated_at = datetime.now(UTC)
    if conv.title == "New chat":
        conv.title = text[:80] + ("…" if len(text) > 80 else "")

    await session.flush()
    await session.refresh(user_msg)
    await session.refresh(assistant_msg)
    await session.refresh(conv)

    return PostMessageOut(
        user_message=_message_out(user_msg),
        assistant_message=_message_out(assistant_msg),
        context=_context_out(conv),
    )
