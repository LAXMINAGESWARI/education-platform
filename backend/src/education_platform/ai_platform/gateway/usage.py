"""Best-effort usage rows. A failed insert never fails the user request."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from education_platform.ai_platform.gateway.models import LlmGatewayCall
from education_platform.db.session import sync_session

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: Decimal | None = None


@dataclass(frozen=True, slots=True)
class UsageRecord:
    feature: str
    step: str
    requested_model: str
    served_model: str | None
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_usd: Decimal | None
    latency_ms: int
    status: str
    error_class: str | None


def usage_from_provider(usage: Any) -> TokenUsage:
    """Read token counts and OpenRouter's usage-accounting cost. Never reads text."""
    if usage is None:
        return TokenUsage()
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    cost_raw = getattr(usage, "cost", None)
    if cost_raw is None:
        extra = getattr(usage, "model_extra", None)
        if isinstance(extra, dict):
            cost_raw = extra.get("cost")
    return TokenUsage(
        prompt_tokens=_nonnegative(getattr(usage, "prompt_tokens", 0)),
        completion_tokens=_nonnegative(getattr(usage, "completion_tokens", 0)),
        cached_tokens=_nonnegative(cached),
        cost_usd=_cost(cost_raw),
    )


def write_usage(record: UsageRecord) -> None:
    """Insert one row. Swallows every error, including a missing table."""
    try:
        session = sync_session()
        try:
            session.add(
                LlmGatewayCall(
                    feature=record.feature,
                    step=record.step,
                    requested_model=record.requested_model,
                    served_model=record.served_model,
                    prompt_tokens=record.prompt_tokens,
                    completion_tokens=record.completion_tokens,
                    cached_tokens=record.cached_tokens,
                    cost_usd=record.cost_usd,
                    latency_ms=record.latency_ms,
                    status=record.status,
                    error_class=record.error_class,
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    except Exception as exc:
        logger.warning(
            "LLM gateway metering failed step=%s error_class=%s",
            record.step,
            type(exc).__name__,
        )


def _nonnegative(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return 0
    value = int(raw)
    return value if value > 0 else 0


def _cost(raw: object) -> Decimal | None:
    if raw is None or isinstance(raw, bool):
        return None
    if not isinstance(raw, int | float | str | Decimal):
        return None
    try:
        value = Decimal(str(raw))
    except Exception:
        return None
    if not value.is_finite() or value < 0:
        return None
    return value
