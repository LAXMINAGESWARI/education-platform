"""One row per screened chat message."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from education_platform.db.base import Base, UUIDPrimaryKeyMixin
from education_platform.db.types import JsonDict, str_enum
from education_platform.modules.guard.policy import GuardDecision, GuardSource


class AssistantGuardVerdict(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "assistant_guard_verdicts"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('allow', 'flag', 'block', 'abuse')",
            name="ck_assistant_guard_verdicts_decision",
        ),
        CheckConstraint(
            "source IN ('jev', 'heuristic', 'fail_open')",
            name="ck_assistant_guard_verdicts_source",
        ),
        CheckConstraint(
            "surface IN ('policy_chat', 'text_to_sql', 'generation_review')",
            name="ck_assistant_guard_verdicts_surface",
        ),
        Index(
            "ix_assistant_guard_verdicts_institution_id_created_at",
            "institution_id",
            "created_at",
        ),
        Index("ix_assistant_guard_verdicts_decision", "decision"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    institution_id: Mapped[UUID] = mapped_column(ForeignKey("institutions.id"))
    role: Mapped[str] = mapped_column(String(32))
    surface: Mapped[str] = mapped_column(String(32))
    message_sha256: Mapped[str] = mapped_column(String(64))
    message_preview: Mapped[str] = mapped_column(String(160))
    injection_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    exfiltration_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    topic: Mapped[str | None] = mapped_column(String(32), nullable=True)
    topic_probabilities: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    decision: Mapped[GuardDecision] = mapped_column(str_enum(GuardDecision, "guard_decision"))
    source: Mapped[GuardSource] = mapped_column(str_enum(GuardSource, "guard_source"))
    jev_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
