"""Pydantic schemas for the self-evaluation module."""

from uuid import UUID

from pydantic import BaseModel

from education_platform.modules.self_evaluation.models import ConfidenceLevel


class SelfEvaluationIn(BaseModel):
    """Payload when a student taps a confidence option."""

    confidence: ConfidenceLevel


class SelfEvaluationOut(BaseModel):
    """The student's latest self-evaluation for a subject (None if never set)."""

    subject_id: UUID
    confidence: ConfidenceLevel | None
