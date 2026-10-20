"""One metering row per gateway call. No prompt or response text."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from education_platform.db.base import Base, UUIDPrimaryKeyMixin


class LlmGatewayCall(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "llm_gateway_calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ok', 'error')",
            name="ck_llm_gateway_calls_status",
        ),
        CheckConstraint("prompt_tokens >= 0", name="ck_llm_gateway_calls_prompt_tokens"),
        CheckConstraint("completion_tokens >= 0", name="ck_llm_gateway_calls_completion_tokens"),
        CheckConstraint("cached_tokens >= 0", name="ck_llm_gateway_calls_cached_tokens"),
        CheckConstraint("latency_ms >= 0", name="ck_llm_gateway_calls_latency_ms"),
        CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0",
            name="ck_llm_gateway_calls_cost_usd",
        ),
        Index("ix_llm_gateway_calls_step_created_at", "step", "created_at"),
    )

    feature: Mapped[str] = mapped_column(String(64))
    step: Mapped[str] = mapped_column(String(80))
    requested_model: Mapped[str] = mapped_column(String(200))
    served_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))
    error_class: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
