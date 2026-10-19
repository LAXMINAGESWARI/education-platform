"""Enrollment and learning-directory API schemas."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from education_platform.modules.assessments.schemas import QuizSummaryOut
from education_platform.modules.materials.schemas import MaterialProgressOut


class GradeEnrollmentOut(BaseModel):
    id: UUID
    academic_period_id: UUID
    academic_period_name: str
    academic_period_status: str
    grade_id: UUID
    grade_name: str
    status: str


class SubjectEnrollmentOut(BaseModel):
    id: UUID
    grade_subject_offering_id: UUID
    academic_period_id: UUID
    academic_period_name: str
    grade_name: str
    subject_id: UUID
    subject_code: str
    subject_name: str
    status: str


class EnrollmentSummary(BaseModel):
    eligible: bool = True
    blocked_reason: str | None = None
    grade_enrollments: list[GradeEnrollmentOut]
    subject_enrollments: list[SubjectEnrollmentOut]


class EnrollMeRequest(BaseModel):
    """POC helper: enroll current student into seeded Grade 8 Mathematics."""

    confirm: bool = True


class DemoBootstrapOut(BaseModel):
    subject_id: UUID
    topic_id: UUID
    topic_title: str
    message: str


class DemoResetOut(BaseModel):
    status: str
    message: str


class SubtopicNodeOut(BaseModel):
    id: UUID
    title: str
    slug: str
    sequence: int
    progress_percent: int
    has_lesson: bool
    lesson_completed: bool
    progress: MaterialProgressOut | None = None
    source_material_version_id: UUID | None = None
    quiz: QuizSummaryOut | None = None


class TopicNodeOut(BaseModel):
    id: UUID
    title: str
    slug: str
    sequence: int
    progress_percent: int
    complete: bool = False
    objectives: list[str] = Field(default_factory=list)
    subtopics: list[SubtopicNodeOut]
    overall_quiz: QuizSummaryOut | None = None
    has_topic_lesson: bool = False
    topic_lesson_completed: bool = False
    topic_lesson_progress: MaterialProgressOut | None = None
    topic_source_material_version_id: UUID | None = None


class SubjectNodeOut(BaseModel):
    id: UUID
    code: str
    name: str
    grade_name: str
    academic_period_name: str
    progress_percent: int
    done: int = 0
    total: int = 0
    topics: list[TopicNodeOut]


class LearningDirectoryOut(BaseModel):
    subjects: list[SubjectNodeOut]
