"""Run-owned generation-review chat. Drafts a change request; never submits one."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.assistant.strategy import (
    AssistantKind,
    AssistantReply,
    ChatTurn,
    GenerationAssistantContext,
    graph_for,
)
from education_platform.modules.assistant.tokens import estimate_tokens
from education_platform.modules.authorization.principal import Principal
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.generation.models import (
    GenerationReviewChat,
    GenerationReviewMessage,
    ReviewDecisionRow,
    ReviewRoundRow,
)
from education_platform.modules.generation.review import (
    _leaf_revision,
    _load_authorised_run,
    _review_stage_for_phase,
)
from education_platform.modules.generation.types import (
    GenerationError,
    ReviewTarget,
    RunPhase,
)

_HISTORY_TURNS = 8


async def post_review_chat(
    session: AsyncSession,
    scope: Scope,
    principal: Principal,
    run_id: UUID,
    *,
    revision_id: UUID,
    message: str,
    target: ReviewTarget | None,
) -> AssistantReply:
    """Run GENERATION_REVIEW. Persists messages on the run. Does not file a decision."""
    text = message.strip()
    if not text:
        raise GenerationError("Message is empty", status_code=400)
    run = await _load_authorised_run(session, scope, run_id)
    if run.phase is RunPhase.PUBLISHED:
        raise GenerationError("Published curriculum is locked.", status_code=409)
    if run.phase not in {RunPhase.OUTLINE_REVIEW, RunPhase.QA_REVIEW}:
        raise GenerationError(
            "Review chat is only available during teacher review.", status_code=409
        )
    stage = _review_stage_for_phase(run.phase)
    revision_row = await _leaf_revision(session, run.id, stage)
    if revision_row is None or revision_row.id != revision_id:
        raise GenerationError(
            "This chat is for a stale revision. Reload the review workspace.",
            status_code=409,
        )
    chat = await _owned_chat(session, run.id, principal.user_id)
    history = await _history(session, chat.id)
    session.add(
        GenerationReviewMessage(
            chat_id=chat.id,
            role="user",
            content=text,
            citations=None,
            draft_change_request=None,
            token_estimate=estimate_tokens(text),
        )
    )
    await session.flush()

    before_decisions = await _decision_count(session, run.id)
    reply = await graph_for(AssistantKind.GENERATION_REVIEW).run_turn(
        session=session,
        principal=principal,
        context=GenerationAssistantContext(
            kind="generation_review",
            run_id=run.id,
            revision_id=revision_row.id,
            target=target,
        ),
        history=history,
        message=text,
    )
    after_decisions = await _decision_count(session, run.id)
    if after_decisions != before_decisions:
        raise GenerationError("Generation review chat cannot submit a decision.", status_code=500)

    session.add(
        GenerationReviewMessage(
            chat_id=chat.id,
            role="assistant",
            content=reply.content,
            citations=_citation_payload(reply),
            draft_change_request=_draft_payload(reply),
            token_estimate=estimate_tokens(reply.content),
        )
    )
    await session.flush()
    return reply


async def _owned_chat(
    session: AsyncSession, run_id: UUID, owner_user_id: UUID
) -> GenerationReviewChat:
    existing = await session.scalar(
        select(GenerationReviewChat).where(
            GenerationReviewChat.run_id == run_id,
            GenerationReviewChat.owner_user_id == owner_user_id,
        )
    )
    if existing is not None:
        return existing
    chat = GenerationReviewChat(run_id=run_id, owner_user_id=owner_user_id)
    session.add(chat)
    await session.flush()
    return chat


async def _history(session: AsyncSession, chat_id: UUID) -> tuple[ChatTurn, ...]:
    rows = (
        await session.scalars(
            select(GenerationReviewMessage)
            .where(GenerationReviewMessage.chat_id == chat_id)
            .order_by(GenerationReviewMessage.created_at.asc())
        )
    ).all()
    turns: list[ChatTurn] = []
    for row in rows:
        if row.role == "user":
            turns.append(ChatTurn(role="user", content=row.content))
        elif row.role == "assistant":
            turns.append(ChatTurn(role="assistant", content=row.content))
    return tuple(turns[-_HISTORY_TURNS:])


async def _decision_count(session: AsyncSession, run_id: UUID) -> int:
    round_ids = list(
        await session.scalars(select(ReviewRoundRow.id).where(ReviewRoundRow.run_id == run_id))
    )
    if not round_ids:
        return 0
    counted = await session.scalar(
        select(func.count())
        .select_from(ReviewDecisionRow)
        .where(ReviewDecisionRow.round_id.in_(tuple(round_ids)))
    )
    return int(counted or 0)


def _citation_payload(reply: AssistantReply) -> list[dict[str, str]] | None:
    if not reply.citations:
        return None
    return [
        {"id": item.id, "label": item.label, "excerpt": item.excerpt} for item in reply.citations
    ]


def _draft_payload(reply: AssistantReply) -> dict[str, object] | None:
    draft = reply.draft_change_request
    if draft is None:
        return None
    target = draft.target
    payload: dict[str, object] = {
        "field": draft.field.value,
        "kind": draft.kind.value,
        "comment": draft.comment,
    }
    if target.kind == "outline_document":
        payload["target"] = {"kind": "outline_document"}
    elif target.kind == "outline_node":
        payload["target"] = {"kind": "outline_node", "node_key": str(target.node_key)}
    elif target.kind == "lesson_section":
        payload["target"] = {"kind": "lesson_section", "section_key": str(target.section_key)}
    else:
        payload["target"] = {"kind": "quiz_item", "item_key": str(target.item_key)}
    return payload
