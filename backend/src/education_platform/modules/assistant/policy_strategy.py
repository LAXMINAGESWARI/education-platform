"""POLICY assistant graph: existing LangGraph handbook retrieve. Admin `/chats` only."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.core.config import get_settings
from education_platform.modules.assistant.graph import run_assistant_turn
from education_platform.modules.assistant.strategy import (
    AssistantContext,
    AssistantKind,
    AssistantReply,
    ChatCitation,
    ChatTurn,
)
from education_platform.modules.audit.service import AuditAction, record_event
from education_platform.modules.authorization.principal import Principal
from education_platform.modules.guard.classifier import message_preview_field


class PolicyAssistantStrategy:
    kind = AssistantKind.POLICY

    async def run_turn(
        self,
        *,
        session: AsyncSession,
        principal: Principal,
        context: AssistantContext,
        history: tuple[ChatTurn, ...],
        message: str,
    ) -> AssistantReply:
        if context.kind != "policy":
            raise TypeError("policy graph requires a policy context")
        result = await run_assistant_turn(
            principal=principal,
            user_message=message,
            history=[{"role": turn.role, "content": turn.content} for turn in history],
        )
        if result.get("injection_blocked") or result.get("question_valid") is False:
            settings = get_settings()
            await record_event(
                session,
                institution_id=principal.institution_id,
                actor_user_id=principal.user_id,
                event_type=AuditAction.ASSISTANT_REFUSED,
                entity_type="policy_chat",
                payload={
                    "surface": "policy",
                    "guard_stage": result.get("guard_stage"),
                    "injection_blocked": bool(result.get("injection_blocked")),
                    "question_valid": result.get("question_valid"),
                    "injection_probability": result.get("injection_probability"),
                    **message_preview_field(message, settings),
                },
            )
        citations: list[ChatCitation] = []
        for item in result.get("citations") or []:
            if not isinstance(item, dict):
                continue
            citation_id = str(item.get("id") or "")
            label = str(item.get("label") or "")
            excerpt = str(item.get("excerpt") or "")
            if citation_id and label and excerpt:
                citations.append(ChatCitation(id=citation_id, label=label, excerpt=excerpt))
        return AssistantReply(
            content=str(result.get("assistant_content") or ""),
            citations=tuple(citations),
            draft_change_request=None,
        )
