"""Insights HTTP response models."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class StudentSummaryOut(BaseModel):
    student_id: UUID
    full_name: str
    student_identifier: str
    grade: str
    section: str | None
    subject: str
    academic_period: str
    quizzes_taken: int
    quizzes_passed: int
    mastery_percent: float
    lessons_completed: int
    attendance_percent: float | None


class StudentSummaryPage(BaseModel):
    scope_description: str
    rows_returned: int
    items: list[StudentSummaryOut]


class SubjectStandingOut(BaseModel):
    subject: str
    quizzes_taken: int
    quizzes_passed: int
    mastery_percent: float
    lessons_started: int
    lessons_completed: int
    last_attempt_at: datetime | None


class AttemptOut(BaseModel):
    attempt_id: UUID
    subject: str
    quiz_title: str
    attempt_number: int
    score_percent: float | None
    passed: bool | None
    submitted_at: datetime | None


class AbsenceOut(BaseModel):
    on_date: date
    status: str


class StudentDetailOut(BaseModel):
    student_id: UUID
    full_name: str
    student_identifier: str
    grade: str
    section: str | None
    academic_period: str
    subjects: list[SubjectStandingOut]
    attempts: list[AttemptOut]
    days_present: int
    days_counted: int
    attendance_percent: float | None
    absences: list[AbsenceOut]


class SubjectSummaryOut(BaseModel):
    grade: str
    subject: str
    students: int
    students_attempted: int
    average_mastery: float | None
    quizzes_taken: int
    quizzes_passed: int


class DashboardSummaryOut(BaseModel):
    scope_description: str
    total_students: int
    average_attendance: float | None
    average_mastery: float | None
    subjects: list[SubjectSummaryOut]
