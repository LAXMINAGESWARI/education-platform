"""Step id → model profile.

Defaults match the model, temperature, and (where one exists) timeout each
caller uses today. Timeouts below are applied only after a feature flag routes
that caller through the gateway.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from education_platform.core.config import Settings
from education_platform.core.llm import OpenRouterError

# Checked against https://openrouter.ai/api/v1/models on 2026-10-20.
# anthropic/claude-3.5-haiku and anthropic/claude-sonnet-4 were not on that list.
# These are current paid ids in the same provider family and tier.
MINI_FALLBACKS: tuple[str, ...] = (
    "google/gemini-2.5-flash",
    "anthropic/claude-haiku-4.5",
)
STRONG_FALLBACKS: tuple[str, ...] = (
    "anthropic/claude-sonnet-4.6",
    "google/gemini-2.5-pro",
)

_TITLES = {
    "policy_chat": "CurioEd Policy Assistant",
    "text_to_sql": "CurioEd Text to SQL",
    "generation": "CurioEd Generation",
    "feedback": "CurioEd Feedback",
    "review_chat": "CurioEd Review Chat",
    "authoring": "CurioEd Authoring",
}


class _ModelSource(StrEnum):
    OPENROUTER = "openrouter_model"
    ADK = "adk_model"
    BLIND = "blind_solver_model"


class _TimeoutKind(StrEnum):
    CHAT = "chat"
    SQL = "sql"
    GUARD = "guard"
    GENERATION = "generation"
    FEEDBACK = "feedback"


class _Tier(StrEnum):
    MINI = "mini"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class _Step:
    feature: str
    model_source: _ModelSource
    temperature: float
    timeout_kind: _TimeoutKind
    concurrency_class: str
    tier: _Tier
    max_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class StepProfile:
    step: str
    feature: str
    title: str
    model: str
    fallback_models: tuple[str, ...]
    temperature: float
    max_tokens: int | None
    timeout_seconds: float
    max_retries: int
    concurrency_class: str


# Temperatures are the values passed today (chat_completion defaults to 0.0).
# ADK settings store a LiteLLM id (``openrouter/openai/...``). The gateway sends
# the plain OpenRouter id.
_STEPS: dict[str, _Step] = {
    "policy_chat.answer": _Step(
        "policy_chat", _ModelSource.OPENROUTER, 0.2, _TimeoutKind.CHAT, "interactive", _Tier.MINI
    ),
    "policy_chat.guard": _Step(
        "policy_chat", _ModelSource.OPENROUTER, 0.0, _TimeoutKind.GUARD, "guard", _Tier.MINI
    ),
    "policy_chat.validator": _Step(
        "policy_chat", _ModelSource.OPENROUTER, 0.0, _TimeoutKind.GUARD, "guard", _Tier.MINI
    ),
    "text_to_sql.generate": _Step(
        "text_to_sql", _ModelSource.OPENROUTER, 0.0, _TimeoutKind.SQL, "interactive", _Tier.MINI
    ),
    "text_to_sql.compose": _Step(
        "text_to_sql", _ModelSource.OPENROUTER, 0.0, _TimeoutKind.CHAT, "interactive", _Tier.MINI
    ),
    "text_to_sql.guard": _Step(
        "text_to_sql", _ModelSource.OPENROUTER, 0.0, _TimeoutKind.GUARD, "guard", _Tier.MINI
    ),
    "text_to_sql.validator": _Step(
        "text_to_sql", _ModelSource.OPENROUTER, 0.0, _TimeoutKind.GUARD, "guard", _Tier.MINI
    ),
    "text_to_sql.intent": _Step(
        "text_to_sql", _ModelSource.OPENROUTER, 0.0, _TimeoutKind.GUARD, "guard", _Tier.MINI
    ),
    "generation.outline": _Step(
        "generation",
        _ModelSource.OPENROUTER,
        0.0,
        _TimeoutKind.GENERATION,
        "generation",
        _Tier.MINI,
    ),
    "generation.lesson": _Step(
        "generation", _ModelSource.ADK, 0.0, _TimeoutKind.GENERATION, "generation", _Tier.MINI
    ),
    "generation.items.writer": _Step(
        "generation", _ModelSource.ADK, 0.0, _TimeoutKind.GENERATION, "generation", _Tier.MINI
    ),
    "generation.items.blind_solve": _Step(
        "generation", _ModelSource.BLIND, 0.0, _TimeoutKind.GENERATION, "generation", _Tier.STRONG
    ),
    "generation.vision": _Step(
        "generation",
        _ModelSource.OPENROUTER,
        0.0,
        _TimeoutKind.GENERATION,
        "generation",
        _Tier.MINI,
    ),
    "generation.curriculum": _Step(
        "generation", _ModelSource.ADK, 0.0, _TimeoutKind.GENERATION, "generation", _Tier.MINI
    ),
    "feedback.summary": _Step(
        "feedback", _ModelSource.OPENROUTER, 0.3, _TimeoutKind.FEEDBACK, "batch", _Tier.MINI
    ),
    "review_chat.reply": _Step(
        "review_chat", _ModelSource.OPENROUTER, 0.0, _TimeoutKind.CHAT, "interactive", _Tier.MINI
    ),
    "authoring.questions": _Step(
        "authoring", _ModelSource.OPENROUTER, 0.4, _TimeoutKind.GENERATION, "generation", _Tier.MINI
    ),
}


def step_ids() -> tuple[str, ...]:
    return tuple(_STEPS)


def resolve(step: str, settings: Settings) -> StepProfile:
    spec = _STEPS.get(step)
    if spec is None:
        raise OpenRouterError(f"Unknown LLM step: {step}")
    model = _model(spec.model_source, settings)
    fallbacks = STRONG_FALLBACKS if spec.tier is _Tier.STRONG else MINI_FALLBACKS
    return StepProfile(
        step=step,
        feature=spec.feature,
        title=_TITLES[spec.feature],
        model=model,
        fallback_models=tuple(item for item in fallbacks if item != model),
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        timeout_seconds=_timeout(spec.timeout_kind, settings),
        max_retries=settings.ai_gateway_max_retries,
        concurrency_class=spec.concurrency_class,
    )


def openrouter_model_id(model: str) -> str:
    """Strip a LiteLLM ``openrouter/`` provider prefix. OpenRouter rejects it."""
    prefix = "openrouter/"
    if model.startswith(prefix):
        return model[len(prefix) :]
    return model


def _model(source: _ModelSource, settings: Settings) -> str:
    if source is _ModelSource.OPENROUTER:
        raw = settings.openrouter_model
    elif source is _ModelSource.ADK:
        raw = settings.adk_model
    elif source is _ModelSource.BLIND:
        raw = settings.blind_solver_model
    else:
        raise OpenRouterError(f"Unknown model source: {source}")
    return openrouter_model_id(raw)


def _timeout(kind: _TimeoutKind, settings: Settings) -> float:
    if kind is _TimeoutKind.CHAT:
        return settings.ai_gateway_timeout_chat_answer_seconds
    if kind is _TimeoutKind.SQL:
        return settings.ai_gateway_timeout_sql_generate_seconds
    if kind is _TimeoutKind.GUARD:
        return settings.ai_gateway_timeout_guard_seconds
    if kind is _TimeoutKind.GENERATION:
        return settings.ai_gateway_timeout_generation_seconds
    if kind is _TimeoutKind.FEEDBACK:
        return settings.ai_gateway_timeout_feedback_seconds
    raise OpenRouterError(f"Unknown timeout kind: {kind}")
