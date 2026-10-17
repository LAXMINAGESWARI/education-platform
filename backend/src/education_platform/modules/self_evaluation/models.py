"""Self-evaluation persistence model.

Records a student's own confidence rating for a subject (how well THEY feel
they understand it), independent of their quiz score. Enables calibration:
"felt confident, scored 85%" vs "felt unsure, aced it".
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from education_platform.db.base import Base, UUIDTimestampMixin
from education_platform.db.types import str_enum


class ConfidenceLevel(str, enum.Enum):
    STILL_LEARNING = "still_learning"
    GETTING_IT = "getting_it"
    CONFIDENT = "confident"


class SelfEvaluation(UUIDTimestampMixin, Base):
    """One self-confidence rating a student gave for a subject."""

    __tablename__ = "self_evaluation"

    student_id: Mapped[UUID] = mapped_column(ForeignKey("student_profiles.id"), index=True)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id"), index=True)
    confidence: Mapped[ConfidenceLevel] = mapped_column(
        str_enum(ConfidenceLevel, "confidence_level")
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
