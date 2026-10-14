"""Read `student_360` through a resolved `Scope`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    QuizAttempt,
    QuizVersion,
)
from education_platform.modules.attendance.models import AttendanceRecord, AttendanceStatus
from education_platform.modules.authorization.predicate import scope_predicate_for
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.insights.models import student_360
from education_platform.modules.insights.register import (
    FINISHED_ATTEMPT_STATUSES,
    STUDENT_360_SCOPE_COLUMNS,
)

MAX_ROWS = 500
MAX_ATTEMPTS = 25
MAX_ABSENCES = 10


@dataclass(frozen=True, slots=True)
class Student360Row:
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


@dataclass(frozen=True, slots=True)
class SubjectStanding:
    subject: str
    quizzes_taken: int
    quizzes_passed: int
    mastery_percent: float
    lessons_started: int
    lessons_completed: int
    last_attempt_at: datetime | None


@dataclass(frozen=True, slots=True)
class AttemptRow:
    attempt_id: UUID
    subject: str
    quiz_title: str
    attempt_number: int
    score_percent: float | None
    passed: bool | None
    submitted_at: datetime | None


@dataclass(frozen=True, slots=True)
class AbsenceRow:
    on_date: date
    status: str


@dataclass(frozen=True, slots=True)
class SubjectSummary:
    """One grade's subject, summarised for the admin dashboard."""

    grade: str
    subject: str
    students: int
    #: Students with at least one quiz attempt -- only they count towards the mastery average.
    students_attempted: int
    average_mastery: float | None
    quizzes_taken: int
    quizzes_passed: int


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    total_students: int
    average_attendance: float | None
    average_mastery: float | None
    #: Weakest first: lowest average mastery, subjects with no attempts yet at the end.
    subjects: list[SubjectSummary]


@dataclass(frozen=True, slots=True)
class StudentDetail:
    student_id: UUID
    full_name: str
    student_identifier: str
    grade: str
    section: str | None
    academic_period: str
    subjects: list[SubjectStanding]
    attempts: list[AttemptRow]
    days_present: int
    days_counted: int
    attendance_percent: float | None
    absences: list[AbsenceRow]


def scope_predicate(scope: Scope) -> ColumnElement[bool]:
    return scope_predicate_for(scope, STUDENT_360_SCOPE_COLUMNS)


def scoped_select(scope: Scope) -> Select[Any]:
    return select(
        student_360.c.student_id,
        student_360.c.full_name,
        student_360.c.student_identifier,
        student_360.c.grade,
        student_360.c.section,
        student_360.c.subject,
        student_360.c.academic_period,
        student_360.c.quizzes_taken,
        student_360.c.quizzes_passed,
        student_360.c.mastery_percent,
        student_360.c.lessons_completed,
        student_360.c.attendance_percent,
    ).where(scope_predicate(scope))


async def query_student_360(
    session: AsyncSession,
    scope: Scope,
    *,
    subject: str | None = None,
    grade: str | None = None,
    section: str | None = None,
    limit: int = 100,
) -> list[Student360Row]:
    statement = scoped_select(scope)

    if subject:
        statement = statement.where(student_360.c.subject == subject)
    if grade:
        statement = statement.where(student_360.c.grade == grade)
    if section:
        statement = statement.where(student_360.c.section == section)

    statement = statement.order_by(
        student_360.c.mastery_percent.asc(), student_360.c.full_name.asc()
    ).limit(max(1, min(limit, MAX_ROWS)))

    result = await session.execute(statement)
    return [
        Student360Row(
            student_id=row.student_id,
            full_name=row.full_name,
            student_identifier=row.student_identifier,
            grade=row.grade,
            section=row.section,
            subject=row.subject,
            academic_period=row.academic_period,
            quizzes_taken=int(row.quizzes_taken or 0),
            quizzes_passed=int(row.quizzes_passed or 0),
            mastery_percent=float(row.mastery_percent or 0.0),
            lessons_completed=int(row.lessons_completed or 0),
            attendance_percent=(
                float(row.attendance_percent) if row.attendance_percent is not None else None
            ),
        )
        for row in result.mappings().all()
    ]


def _round(value: Any) -> float | None:
    return None if value is None else round(float(value), 1)


async def dashboard_summary(session: AsyncSession, scope: Scope) -> DashboardSummary:
    """Headline numbers and per-subject standing, aggregated in the database.

    Everything goes through the same `scope_predicate` as the register, so an administrator
    gets the institution, a teacher their own classes and a student themselves -- this adds
    no second definition of who may see what.

    Mastery only counts rows with ``quizzes_taken > 0``: `student_360` reports a never-quizzed
    subject as 0%, and averaging those in would drag every number down (the same trap PR #134
    fixed in the at-risk engine).
    """
    attempted = student_360.c.quizzes_taken > 0

    # Attendance is whole-day, so it repeats on every subject row of a student: collapse to
    # one value per student before averaging, or students taking more subjects weigh more.
    per_student = (
        select(
            student_360.c.student_id,
            func.max(student_360.c.attendance_percent).label("attendance_percent"),
        )
        .where(scope_predicate(scope))
        .group_by(student_360.c.student_id)
        .subquery()
    )
    headline = (
        await session.execute(
            select(
                func.count(per_student.c.student_id),
                func.avg(per_student.c.attendance_percent),
            )
        )
    ).one()

    overall_mastery = (
        await session.execute(
            select(func.avg(student_360.c.mastery_percent).filter(attempted)).where(
                scope_predicate(scope)
            )
        )
    ).scalar_one()

    subject_mastery = func.avg(student_360.c.mastery_percent).filter(attempted)
    subject_rows = (
        await session.execute(
            select(
                student_360.c.grade,
                student_360.c.subject,
                func.count(student_360.c.student_id.distinct()).label("students"),
                func.count(student_360.c.student_id.distinct())
                .filter(attempted)
                .label("students_attempted"),
                subject_mastery.label("average_mastery"),
                func.coalesce(func.sum(student_360.c.quizzes_taken), 0).label("quizzes_taken"),
                func.coalesce(func.sum(student_360.c.quizzes_passed), 0).label("quizzes_passed"),
            )
            .where(scope_predicate(scope))
            .group_by(student_360.c.grade, student_360.c.subject)
            .order_by(
                subject_mastery.asc().nulls_last(),
                student_360.c.grade.asc(),
                student_360.c.subject.asc(),
            )
        )
    ).mappings()

    return DashboardSummary(
        total_students=int(headline[0] or 0),
        average_attendance=_round(headline[1]),
        average_mastery=_round(overall_mastery),
        subjects=[
            SubjectSummary(
                grade=row["grade"],
                subject=row["subject"],
                students=int(row["students"]),
                students_attempted=int(row["students_attempted"]),
                average_mastery=_round(row["average_mastery"]),
                quizzes_taken=int(row["quizzes_taken"]),
                quizzes_passed=int(row["quizzes_passed"]),
            )
            for row in subject_rows
        ],
    )


async def student_detail(
    session: AsyncSession,
    scope: Scope,
    student_id: UUID,
) -> StudentDetail | None:
    """One student in scope, or None (indistinguishable from not existing)."""
    register = (
        (
            await session.execute(
                select(
                    student_360.c.student_id,
                    student_360.c.full_name,
                    student_360.c.student_identifier,
                    student_360.c.grade,
                    student_360.c.section,
                    student_360.c.academic_period,
                    student_360.c.subject,
                    student_360.c.student_subject_enrollment_id,
                    student_360.c.quizzes_taken,
                    student_360.c.quizzes_passed,
                    student_360.c.mastery_percent,
                    student_360.c.lessons_started,
                    student_360.c.lessons_completed,
                    student_360.c.last_attempt_at,
                    student_360.c.days_present,
                    student_360.c.days_counted,
                    student_360.c.attendance_percent,
                )
                .where(scope_predicate(scope), student_360.c.student_id == student_id)
                .order_by(student_360.c.subject.asc())
            )
        )
        .mappings()
        .all()
    )

    if not register:
        return None

    head = register[0]
    subject_of_enrolment = {
        row.student_subject_enrollment_id: row.subject
        for row in register
        if row.student_subject_enrollment_id is not None
    }

    return StudentDetail(
        student_id=head.student_id,
        full_name=head.full_name,
        student_identifier=head.student_identifier,
        grade=head.grade,
        section=head.section,
        academic_period=head.academic_period,
        subjects=[
            SubjectStanding(
                subject=row.subject,
                quizzes_taken=int(row.quizzes_taken or 0),
                quizzes_passed=int(row.quizzes_passed or 0),
                mastery_percent=float(row.mastery_percent or 0.0),
                lessons_started=int(row.lessons_started or 0),
                lessons_completed=int(row.lessons_completed or 0),
                last_attempt_at=row.last_attempt_at,
            )
            for row in register
        ],
        attempts=await _permitted_attempts(session, student_id, subject_of_enrolment),
        days_present=int(head.days_present or 0),
        days_counted=int(head.days_counted or 0),
        attendance_percent=(
            float(head.attendance_percent) if head.attendance_percent is not None else None
        ),
        absences=await _recent_absences(session, student_id),
    )


async def _permitted_attempts(
    session: AsyncSession,
    student_id: UUID,
    subject_of_enrolment: dict[UUID, str],
) -> list[AttemptRow]:
    if not subject_of_enrolment:
        return []

    rows = await session.execute(
        select(
            QuizAttempt.id,
            QuizAttempt.student_subject_enrollment_id,
            QuizAttempt.attempt_number,
            QuizAttempt.score_percent,
            QuizAttempt.passed,
            QuizAttempt.submitted_at,
            CommonMasteryQuiz.title,
        )
        .join(QuizVersion, QuizVersion.id == QuizAttempt.quiz_version_id)
        .join(CommonMasteryQuiz, CommonMasteryQuiz.id == QuizVersion.quiz_id)
        .where(
            QuizAttempt.student_id == student_id,
            QuizAttempt.student_subject_enrollment_id.in_(subject_of_enrolment),
            QuizAttempt.status.in_(FINISHED_ATTEMPT_STATUSES),
        )
        .order_by(QuizAttempt.submitted_at.desc().nullslast())
        .limit(MAX_ATTEMPTS)
    )

    return [
        AttemptRow(
            attempt_id=row.id,
            subject=subject_of_enrolment[row.student_subject_enrollment_id],
            quiz_title=row.title,
            attempt_number=int(row.attempt_number),
            score_percent=(float(row.score_percent) if row.score_percent is not None else None),
            passed=row.passed,
            submitted_at=row.submitted_at,
        )
        for row in rows.all()
    ]


async def _recent_absences(session: AsyncSession, student_id: UUID) -> list[AbsenceRow]:
    rows = await session.execute(
        select(AttendanceRecord.on_date, AttendanceRecord.status)
        .where(
            AttendanceRecord.student_id == student_id,
            AttendanceRecord.grade_subject_offering_id.is_(None),
            AttendanceRecord.status != AttendanceStatus.PRESENT,
        )
        .order_by(AttendanceRecord.on_date.desc())
        .limit(MAX_ABSENCES)
    )
    return [AbsenceRow(on_date=on_date, status=status.value) for on_date, status in rows.all()]
