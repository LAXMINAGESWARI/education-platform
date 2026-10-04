"""Pydantic schemas for the slide engagement module."""

from uuid import UUID

from pydantic import BaseModel, Field


class SlideEngagementIn(BaseModel):
    """Payload the frontend sends when flushing a slide's active time."""

    slide_index: int = Field(..., ge=0)
    seconds_active: int = Field(..., ge=2, description="Min 2s to filter noise")


class SlideEngagementOut(BaseModel):
    """Confirmation returned to the caller."""

    recorded: bool


class SlideEngagementSummary(BaseModel):
    """Per-slide active time for one student on one subtopic."""

    slide_index: int
    total_seconds: int


class SubtopicEngagementSummary(BaseModel):
    """All slide summaries for a subtopic, used by the student dashboard."""

    subtopic_id: UUID
    slides: list[SlideEngagementSummary]
