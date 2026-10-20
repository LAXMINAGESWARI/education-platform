"""Data-assistant guards for the lean pipeline.

Separate mode calls the existing injection and off-topic nodes in order. Batch
mode sends ``DATA_INJECTION`` and ``DATA_OFF_TOPIC`` in one ``post_decisions``
request. The regex pre-check, fail-open when unconfigured, fail-closed on a
live error, and the HTTP 402 GPT fallback stay the same either way.
"""

from __future__ import annotations

import math
from typing import Any

from education_platform.core.config import Settings, get_settings
from education_platform.modules.guard.classifier import (
    DATA_INJECTION,
    DATA_OFF_TOPIC,
    guard_state,
    is_positive,
)
from education_platform.modules.judge.client import post_decisions
from education_platform.modules.judge.errors import JudgeError, JudgePaymentRequired
from education_platform.modules.judge.types import NoulAnswer
from education_platform.modules.text_to_sql.meter import note_llm_call, note_tokens
from education_platform.modules.text_to_sql.nodes.injection_guard import (
    _CLASSIFIER_BLOCKED_REPLY,
    _GUARD_UNAVAILABLE_REPLY,
    _heuristic_injection,
    gpt_injection_decision,
    injection_guard,
)
from education_platform.modules.text_to_sql.nodes.question_validator import (
    _OFF_TOPIC_REPLY,
    _gpt_off_topic_fallback,
    question_validator,
)
from education_platform.modules.text_to_sql.pipeline import guard_batch_enabled
from education_platform.modules.text_to_sql.state import (
    INJECTION_BLOCKED,
    OFF_TOPIC_REJECTED,
    TextToSQLState,
    format_error,
)


def _audit(state: TextToSQLState, **fields: Any) -> dict[str, Any]:
    return {**(state.get("audit_entry") or {}), **fields}


def _unit_noul(answer: object, key: str) -> float:
    if not isinstance(answer, NoulAnswer):
        raise JudgeError(f"Decisions response missing the {key} noul answer")
    noul = answer.noul
    if isinstance(noul, float) and math.isnan(noul):
        raise JudgeError("Jev returned a NaN noul.")
    if not isinstance(noul, (int, float)) or isinstance(noul, bool) or noul < 0.0 or noul > 1.0:
        raise JudgeError("Jev returned a noul outside 0-1.")
    return float(noul)


async def _batched(state: TextToSQLState, settings: Settings) -> TextToSQLState:
    question = state.get("question") or ""
    try:
        note_llm_call()
        response = await post_decisions(
            state=guard_state(question),
            questions={"injection": DATA_INJECTION, "off_topic": DATA_OFF_TOPIC},
            settings=settings,
        )
    except JudgePaymentRequired:
        return await _gpt_both(state, question, settings)
    except JudgeError:
        return {
            **state,
            "audit_entry": _audit(state, guard_stage="jev_error"),
            "error": format_error(INJECTION_BLOCKED, _GUARD_UNAVAILABLE_REPLY),
        }

    note_tokens(response.input_tokens, response.output_tokens)
    try:
        injection_probability = _unit_noul(response.answers.get("injection"), "injection")
        off_topic_probability = _unit_noul(response.answers.get("off_topic"), "off_topic")
    except JudgeError:
        return {
            **state,
            "audit_entry": _audit(state, guard_stage="jev_error"),
            "error": format_error(INJECTION_BLOCKED, _GUARD_UNAVAILABLE_REPLY),
        }

    noted = _audit(
        state,
        guard_stage="jev",
        injection_probability=injection_probability,
        off_topic_probability=off_topic_probability,
        guard_batch=True,
    )
    if is_positive(injection_probability, settings):
        return {
            **state,
            "audit_entry": noted,
            "error": format_error(INJECTION_BLOCKED, _CLASSIFIER_BLOCKED_REPLY),
        }
    if is_positive(off_topic_probability, settings):
        return {
            **state,
            "audit_entry": noted,
            "error": format_error(OFF_TOPIC_REJECTED, _OFF_TOPIC_REPLY),
        }
    # Same OR as the legacy nodes. Generation is already running beside this
    # task. checking/done and SQL wait until this returns.
    passed: TextToSQLState = {**state, "audit_entry": noted}
    decided = await gpt_injection_decision(passed, question, settings, jev_passed=True)
    if decided.get("error"):
        return decided
    return await _gpt_off_topic_fallback(decided, question, settings)


async def _gpt_both(state: TextToSQLState, question: str, settings: Settings) -> TextToSQLState:
    """HTTP 402: the legacy GPT injection decision, then the legacy off-topic one."""
    decided = await gpt_injection_decision(state, question, settings, jev_passed=False)
    if decided.get("error"):
        return decided
    return await _gpt_off_topic_fallback(decided, question, settings)


async def run_data_guards(state: TextToSQLState) -> TextToSQLState:
    """Injection, then off-topic. One Jev request when batching is on."""
    settings = get_settings()
    question = state.get("question") or ""
    if _heuristic_injection(question) or not guard_batch_enabled(settings):
        guarded = await injection_guard(state)
        if guarded.get("error"):
            return guarded
        return await question_validator(guarded)
    if not settings.openrouter_configured:
        guarded = await injection_guard(state)
        if guarded.get("error"):
            return guarded
        return await question_validator(guarded)
    return await _batched({**state, "pipeline": state.get("pipeline") or "lean"}, settings)
