from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class TopicSummary(BaseModel):
    id: str
    title: str
    has_lesson: bool
    has_quiz: bool


class LessonSlide(BaseModel):
    number: int
    title: str
    content: str


class MaterialProgressUpdate(BaseModel):
    status: Literal["opened", "completed"]
    last_unit_ordinal: int | None = Field(default=None, ge=1)


class MaterialProgressOut(BaseModel):
    status: str
    opened_at: datetime
    last_opened_at: datetime
    completed_at: datetime | None
    last_unit_ordinal: int | None
    source_material_version_id: UUID


class LessonMaterial(BaseModel):
    id: str
    title: str
    markdown: str
    slides: list[LessonSlide] = Field(default_factory=list)
    progress: MaterialProgressOut | None = None
    source_material_version_id: UUID | None = None
    published_at: datetime | None = None
    quiz_unlocked: bool = False
    quiz_id: UUID | None = None
