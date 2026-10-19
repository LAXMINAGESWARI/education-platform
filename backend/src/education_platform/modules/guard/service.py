"""Screen one user message, persist the verdict, and return the action to take."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.core.config import Settings, get_settings
from education_platform.modules.guard.heuristic import obvious_injection
from education_platform.modules.guard.jev_client import DecisionsResponse, JevError, post_decisions
from education_platform.modules.guard.models import AssistantGuardVerdict
from education_platform.modules.guard.policy import (
    ABUSE_REPLY,
    BLOCK_REPLY,
    TOPICS,
    GuardDecision,
    GuardSource,
    decide,
    guard_questions,
    guard_state,
)
from education_platform.modules.guard.service_types import GuardTurn

logger = logging.getLogger(__name__)

SURFACE_POLICY = "policy_chat"
SURFACE_TEXT_TO_SQL = "text_to_sql"
SURFACE_GENERATION = "generation_review"
_PREVIEW_CHARS = 160
_ERROR_CHARS = 500


@dataclass(frozen=True, slots=True)
class GuardOutcome:
    decision: GuardDecision
    source: GuardSource
    injection_probability: float | None
    exfiltration_probability: float | None
    topic: str | None
    topic_probabilities: dict[str, float] | None
    latency_ms: int
    jev_error: str | None
    reply: str | None

    @property
    def hardened(self) -> bool:
        return self.decision is GuardDecision.FLAG


def role_name(roles: Iterable[str]) -> str:
    held = set(roles)
    if "administrator" in held:
        return "administrator"
    if "teacher" in held:
        return "teacher"
    if "student" in held:
        return "student"
    return "unknown"


def _reply_for(decision: GuardDecision) -> str | None:
    if decision is GuardDecision.BLOCK:
        return BLOCK_REPLY
    if decision is GuardDecision.ABUSE:
        return ABUSE_REPLY
    return None


def _safe_error(exc: JevError) -> str:
    text = " ".join(str(exc).split())
    if "Bearer " in text or "sk-or-" in text:
        return "redacted"
    return text[:_ERROR_CHARS]


def _noul(answers: dict[str, Any], key: str) -> float:
    raw = answers.get(key)
    if not isinstance(raw, dict) or raw.get("type") != "noul":
        raise JevError(f"{key} answer was not a noul")
    value = raw.get("noul")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JevError(f"{key} noul was not a number")
    probability = float(value)
    if probability < 0.0 or probability > 1.0:
        raise JevError(f"{key} noul was outside 0..1")
    return probability


def _topic(answers: dict[str, Any]) -> tuple[str, dict[str, float]]:
    raw = answers.get("topic")
    if not isinstance(raw, dict) or raw.get("type") != "choice":
        raise JevError("topic answer was not a choice")
    choice = raw.get("choice")
    if not isinstance(choice, str) or choice not in TOPICS:
        raise JevError("topic choice was not one of the guard labels")
    probabilities: dict[str, float] = {}
    raw_probs = raw.get("probabilities")
    if isinstance(raw_probs, dict):
        for key, value in raw_probs.items():
            if (
                isinstance(key, str)
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                probabilities[key] = float(value)
    return choice, probabilities


def _from_answers(
    response: DecisionsResponse, *, block_at: float, flag_at: float
) -> tuple[GuardDecision, float, float, str, dict[str, float]]:
    injection = _noul(response.answers, "injection")
    exfiltration = _noul(response.answers, "data_exfiltration")
    topic, probabilities = _topic(response.answers)
    decision = decide(
        injection=injection,
        exfiltration=exfiltration,
        topic=topic,
        block_at=block_at,
        flag_at=flag_at,
    )
    return decision, injection, exfiltration, topic, probabilities


async def screen_chat(
    session: AsyncSession,
    *,
    user_id: UUID,
    institution_id: UUID,
    role: str,
    surface: str,
    message: str,
    history: Sequence[GuardTurn] = (),
    settings: Settings | None = None,
) -> GuardOutcome:
    """Ask Jev, fall open to the heuristic on errors, and insert a verdict row.

    A refusal ``reply`` means the caller must not call the assistant model.
    ``hardened`` means the caller should add the reminder to that model's system prompt.
    Authorization stays in the route and in SQL scope predicates; this does not grant access.
    """
    cfg = settings or get_settings()
    started = time.perf_counter()
    decision: GuardDecision
    source: GuardSource
    injection: float | None = None
    exfiltration: float | None = None
    topic: str | None = None
    topic_probabilities: dict[str, float] | None = None
    jev_error: str | None = None

    try:
        response = await post_decisions(
            state=guard_state(message=message, role=role, history=list(history)),
            questions=guard_questions(),
            settings=cfg,
        )
        decision, injection, exfiltration, topic, topic_probabilities = _from_answers(
            response,
            block_at=cfg.jev_block_threshold,
            flag_at=cfg.jev_flag_threshold,
        )
        source = GuardSource.JEV
    except JevError as exc:
        jev_error = _safe_error(exc)
        if obvious_injection(message):
            decision = GuardDecision.BLOCK
            source = GuardSource.HEURISTIC
        else:
            decision = GuardDecision.ALLOW
            source = GuardSource.FAIL_OPEN
        logger.warning(
            "assistant guard jev failure surface=%s status=%s decision=%s source=%s",
            surface,
            exc.status_code,
            decision.value,
            source.value,
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    preview = message[:_PREVIEW_CHARS]
    session.add(
        AssistantGuardVerdict(
            user_id=user_id,
            institution_id=institution_id,
            role=role[:32],
            surface=surface,
            message_sha256=hashlib.sha256(message.encode()).hexdigest(),
            message_preview=preview,
            injection_probability=injection,
            exfiltration_probability=exfiltration,
            topic=topic,
            topic_probabilities=topic_probabilities,
            decision=decision,
            source=source,
            jev_error=jev_error,
            latency_ms=latency_ms,
        )
    )
    await session.flush()
    return GuardOutcome(
        decision=decision,
        source=source,
        injection_probability=injection,
        exfiltration_probability=exfiltration,
        topic=topic,
        topic_probabilities=topic_probabilities,
        latency_ms=latency_ms,
        jev_error=jev_error,
        reply=_reply_for(decision),
    )


async def list_flagged_verdicts(
    session: AsyncSession,
    *,
    institution_id: UUID,
    limit: int,
) -> list[AssistantGuardVerdict]:
    """Blocked, flagged, and abuse refusals for one institution, newest first."""
    rows = (
        await session.scalars(
            select(AssistantGuardVerdict)
            .where(
                AssistantGuardVerdict.institution_id == institution_id,
                AssistantGuardVerdict.decision.in_(
                    (GuardDecision.BLOCK, GuardDecision.FLAG, GuardDecision.ABUSE)
                ),
            )
            .order_by(AssistantGuardVerdict.created_at.desc())
            .limit(limit)
        )
    ).all()
    return list(rows)
