"""Quiz attempt and presentation schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class QuizOption(BaseModel):
    label: str
    text: str


class QuizQuestion(BaseModel):
    number: int
    difficulty: str | None = None
    prompt: str
    options: list[QuizOption]


class QuizMaterial(BaseModel):
    id: UUID
    title: str
    questions: list[QuizQuestion]
    pass_threshold_percent: Decimal
    duration_seconds: int | None = None
    max_attempts: int | None = None
    result_release_mode: str


class AttemptHistoryItem(BaseModel):
    id: UUID
    attempt_number: int
    status: str
    score_percent: Decimal | None
    passed: bool | None
    submitted_at: datetime | None
    started_at: datetime | None


class QuizSummaryOut(BaseModel):
    id: UUID | None = None
    title: str | None = None
    scope: Literal["subtopic_mastery", "topic_mastery"]
    available: bool = False
    unlocked: bool = False
    locked_reason: str | None = None
    pass_threshold_percent: Decimal | None = None
    attempt_count: int = 0
    best_score_percent: Decimal | None = None
    passed: bool = False
    in_progress_attempt_id: UUID | None = None
    recent_attempts: list[AttemptHistoryItem] = Field(default_factory=list)


class StartAttemptResponse(BaseModel):
    id: UUID
    quiz_id: UUID
    quiz_version_id: UUID
    attempt_number: int
    status: str
    started_at: datetime | None
    deadline_at: datetime | None
    pass_threshold_percent: Decimal
    result_release_mode: str
    questions: list[QuizQuestion]
    title: str
    scope: str
    target_id: UUID


class AnswerSubmission(BaseModel):
    question_number: int = Field(ge=1)
    selected_option_label: str = Field(min_length=1, max_length=10)


class SubmitAttemptRequest(BaseModel):
    answers: list[AnswerSubmission]


class AttemptAnswerOut(BaseModel):
    question_number: int
    selected_option_label: str | None
    is_correct: bool | None
    marks_awarded: Decimal | None


class RemediationOut(BaseModel):
    material_id: UUID | None = None
    material_title: str | None = None
    retake_quiz_id: UUID | None = None


class SubtopicAttemptScore(BaseModel):
    sequence: int
    percent: Decimal
    submitted_at: datetime | None


class SetGoalRequest(BaseModel):
    target_percent: Decimal = Field(ge=0, le=100)
    due_at: datetime


class GoalOut(BaseModel):
    subtopic_id: UUID
    subtopic_name: str
    target_percent: Decimal
    due_at: datetime
    current_percent: Decimal | None
    status: Literal["on_track", "achieved", "missed"]


class SubtopicFeedback(BaseModel):
    subtopic_id: UUID
    subtopic_name: str
    topic_name: str
    percent: Decimal
    question_count: int
    attempts: list[SubtopicAttemptScore] = Field(default_factory=list)
    percent_easy: Decimal | None = None
    easy_question_count: int = 0
    percent_medium: Decimal | None = None
    medium_question_count: int = 0
    percent_hard: Decimal | None = None
    hard_question_count: int = 0
    prior_percent: Decimal | None = None
    delta: Decimal | None = None
    class_avg_percent: Decimal | None = None
    class_sample_size: int = 0
    is_weak: bool | None = None
    remediation: RemediationOut | None = None
    goal: GoalOut | None = None


class RegressionOut(BaseModel):
    subtopic_id: UUID
    subtopic_name: str
    regressed_question_count: int


class SubjectFeedbackDashboard(BaseModel):
    subject_id: UUID
    subject_name: str
    overall_percent: Decimal | None
    attempt_count: int
    subtopics: list[SubtopicFeedback]
    strength_subtopic_id: UUID | None
    top_focus_subtopic_ids: list[UUID]
    regressions: list[RegressionOut] = Field(default_factory=list)
    summary: str


class ReviewNudgeOut(BaseModel):
    subtopic_id: UUID
    subtopic_name: str
    topic_name: str
    days_since_last_attempt: int
    due_interval_days: int
    remediation: RemediationOut | None = None


class TrajectoryOut(BaseModel):
    subtopic_id: UUID
    subtopic_name: str
    trend: Literal["improving", "flat", "declining"]
    recent_change: Decimal | None
    evidence_level: Literal["low", "medium"]


class FeedbackHighlights(BaseModel):
    due_reviews: list[ReviewNudgeOut] = Field(default_factory=list)
    trajectories: list[TrajectoryOut] = Field(default_factory=list)


class AttemptResult(BaseModel):
    id: UUID
    quiz_id: UUID
    target_id: UUID | None = None
    scope: str | None = None
    attempt_number: int
    status: str
    started_at: datetime | None
    deadline_at: datetime | None = None
    submitted_at: datetime | None
    scored_at: datetime | None
    score_raw: Decimal | None
    score_percent: Decimal | None
    pass_threshold_percent: Decimal | None = None
    passed: bool | None
    review_available: bool = True
    answers: list[AttemptAnswerOut] = Field(default_factory=list)
