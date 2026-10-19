"""Stored Jev judgments. Enforce rows can fail an item or hold a lesson."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from education_platform.db.base import Base, UUIDTimestampMixin
from education_platform.db.types import JsonDict

_KINDS = "subject_kind IN ('lesson_section', 'lesson', 'quiz_item')"
_MODES = "mode IN ('off', 'shadow', 'enforce')"
_DECISIONS = "decision IN ('accept', 'needs_review', 'reject', 'error')"


class JudgeEvaluation(UUIDTimestampMixin, Base):
    __tablename__ = "judge_evaluations"
    __table_args__ = (
        CheckConstraint(_KINDS, name="ck_judge_evaluations_subject_kind"),
        CheckConstraint(_MODES, name="ck_judge_evaluations_mode"),
        CheckConstraint(_DECISIONS, name="ck_judge_evaluations_decision"),
        CheckConstraint("cost_usd >= 0", name="ck_judge_evaluations_cost_usd"),
        CheckConstraint("latency_ms >= 0", name="ck_judge_evaluations_latency_ms"),
        CheckConstraint("input_tokens >= 0", name="ck_judge_evaluations_input_tokens"),
        CheckConstraint("output_tokens >= 0", name="ck_judge_evaluations_output_tokens"),
    )

    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("content_generation_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    generation_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    question_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("question_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    subject_kind: Mapped[str] = mapped_column(String(32))
    subject_key: Mapped[UUID | None] = mapped_column(nullable=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    mode: Mapped[str] = mapped_column(String(16))
    decision: Mapped[str] = mapped_column(String(16))
    reasons: Mapped[list[Any]] = mapped_column(JsonDict)
    checks: Mapped[list[Any]] = mapped_column(JsonDict)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 8), default=Decimal("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    blind_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    blind_answer: Mapped[str | None] = mapped_column(String(8), nullable=True)
    gate_verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gate_servable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
