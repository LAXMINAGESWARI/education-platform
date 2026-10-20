"""Policy-chat guard stages, in the same order as ``graph.py``.

``injection_guard`` then ``question_validator``:

1. regex (``_heuristic_injection``)
2. Jev injection (``POLICY_INJECTION`` only)
3. messages shorter than 3 characters
4. GPT scope (``gpt_scope`` via ``_policy_json``)

Policy off-topic is not ``POLICY_OUT_OF_SCOPE``. Data text-to-SQL still uses
``DATA_OFF_TOPIC``. Review is regex plus Jev injection and does not come here.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from education_platform.core.config import Settings
from education_platform.core.llm import OpenRouterError
from education_platform.modules.assistant import graph
from education_platform.modules.assistant.contracts import (
    AssistantGraphState,
    QuestionValidatorResult,
)
from education_platform.modules.judge.errors import JudgeError, JudgePaymentRequired

POLICY_PIPELINE = ("regex", "jev_injection", "short", "gpt_scope")

_EVAL_USER = "00000000-0000-0000-0000-000000000001"
_EVAL_INSTITUTION = "00000000-0000-0000-0000-000000000002"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """``stage`` is the stage that refused. An allow leaves it empty."""

    refused: bool
    stage: str | None
    checks: tuple[str, ...]


def _transcript(message: str, history: Sequence[tuple[str, str]]) -> str:
    current = AssistantGraphState(
        user_message=message,
        history=[{"role": role, "content": content} for role, content in history],
        user_id=_EVAL_USER,
        institution_id=_EVAL_INSTITUTION,
        user_email="guard-eval@example.com",
        roles=["administrator"],
    )
    return graph._classifier_transcript(current)


async def run_policy_pipeline(
    message: str,
    *,
    settings: Settings,
    history: Sequence[tuple[str, str]] = (),
) -> PolicyDecision:
    """Score one policy-chat message. Looks up ``graph`` helpers at call time."""
    checks: list[str] = ["regex"]
    if graph._heuristic_injection(message):
        return PolicyDecision(True, "regex", tuple(checks))

    if settings.openrouter_configured:
        checks.append("jev_injection")
        try:
            probability = await graph.classify_noul(
                state=graph.guard_state(message, history),
                question=graph.POLICY_INJECTION,
                settings=settings,
            )
        except JudgePaymentRequired:
            refused = await _gpt_injection(message, history, settings)
            checks.append("gpt_injection")
            if refused is None:
                return PolicyDecision(True, "gpt_error", tuple(checks))
            if refused:
                return PolicyDecision(True, "gpt", tuple(checks))
        except JudgeError:
            return PolicyDecision(True, "jev_error", tuple(checks))
        else:
            if graph.is_positive(probability, settings):
                return PolicyDecision(True, "jev_injection", tuple(checks))

    checks.append("short")
    if len(message.strip()) < 3:
        return PolicyDecision(True, "short", tuple(checks))
    if not settings.openrouter_configured:
        return PolicyDecision(False, None, tuple(checks))

    checks.append("gpt_scope")
    try:
        data = await graph._policy_json(
            "policy_chat.validator",
            [
                {"role": "system", "content": graph.POLICY_SCOPE_SYSTEM},
                {"role": "user", "content": _transcript(message, history)},
            ],
            settings=settings,
        )
        classified = QuestionValidatorResult.model_validate(data)
    except (OpenRouterError, json.JSONDecodeError, TypeError, ValidationError):
        return PolicyDecision(True, "gpt_error", tuple(checks))
    if not classified.valid:
        return PolicyDecision(True, "gpt_scope", tuple(checks))
    return PolicyDecision(False, None, tuple(checks))


async def _gpt_injection(
    message: str,
    history: Sequence[tuple[str, str]],
    settings: Settings,
) -> bool | None:
    """HTTP 402 injection fallback. ``None`` means the GPT call failed closed."""
    try:
        return await graph.legacy_gpt_flag(
            settings=settings,
            system=graph.POLICY_GPT_INJECTION,
            user=_transcript(message, history),
            field="injection",
        )
    except JudgeError:
        return None
