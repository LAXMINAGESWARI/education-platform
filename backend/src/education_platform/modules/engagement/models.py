"""Slide engagement persistence model.

Records how long a student actively spent on each lesson slide.
Used as a student-facing signal in the struggle flag / student dashboard.
Intentionally kept separate from at_risk_flags (teacher view).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from education_platform.db.base import Base, UUIDTimestampMixin


class SlideEngagement(UUIDTimestampMixin, Base):
    """Active seconds a student spent on a single lesson slide."""

    __tablename__ = "slide_engagement"

    # FK to student_profiles.id (matches at_risk pattern — not users.id)
    student_id: Mapped[UUID] = mapped_column(ForeignKey("student_profiles.id"), index=True)
    subtopic_id: Mapped[UUID] = mapped_column(ForeignKey("subtopics.id"), index=True)
    slide_index: Mapped[int] = mapped_column(Integer)
    seconds_active: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
