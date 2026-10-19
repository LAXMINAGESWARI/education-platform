"""At-risk flag persistence and scoped reads."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.academics.models import GradeSubjectOffering, Subject
from education_platform.modules.assessments.models import QuizAttempt
from education_platform.modules.at_risk.engine import (
    DEFAULT_THRESHOLDS,
    EngineFlag,
    StudentSignals,
    SubjectSignal,
    Thresholds,
    evaluate_student,
)
from education_platform.modules.at_risk.models import AtRiskFlag, AtRiskStatus, AtRiskTier
from education_platform.modules.audit.service import AuditAction, record_event
from education_platform.modules.auth.models import StudentProfile
from education_platform.modules.authorization.predicate import ScopeColumns, scope_predicate_for
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.insights.models import student_360
from education_platform.modules.insights.register import (
    FINISHED_ATTEMPT_STATUSES,
    STUDENT_360_IDENTITY_COLUMNS,
    STUDENT_360_SIGNAL_COLUMNS,
)

AT_RISK_FLAG_COLUMNS = ScopeColumns(
    institution_id=AtRiskFlag.institution_id,
    student_id=AtRiskFlag.student_id,
    grade_subject_offering_id=AtRiskFlag.grade_subject_offering_id,
    section_id=AtRiskFlag.section_id,
)


def flag_scope_predicate(scope: Scope) -> ColumnElement[bool]:
    """Permission boundary for `at_risk_flags` — real `section_id` required for teachers."""
    return scope_predicate_for(scope, AT_RISK_FLAG_COLUMNS)


@dataclass(frozen=True, slots=True)
class FlagRow:
    id: UUID
    student_id: UUID
    student_name: str
    grade_subject_offering_id: UUID | None
    subject: str | None
    tier: str
    drivers: list[dict[str, object]]
    status: str
    computed_at: datetime
    dismissed_by_user_id: UUID | None
    dismissed_at: datetime | None
    dismissal_note: str | None


# Labels the assistant and the flag page both use. Order is fixed so a student
# with several drivers reads the same way every time.
REASON_ORDER: tuple[str, ...] = ("low attendance", "low mastery", "declining trend")
_REASON_BY_METRIC: dict[str, str] = {
    "attendance_percent": "low attendance",
    "mastery_percent": "low mastery",
    "mastery_trend": "declining trend",
}


def reasons_from_drivers(drivers: list[dict[str, object]]) -> tuple[str, ...]:
    """Map engine driver metrics onto the three reasons a person can be at risk."""
    found: set[str] = set()
    unknown: list[str] = []
    for driver in drivers:
        metric = driver.get("metric")
        if not isinstance(metric, str):
            continue
        label = _REASON_BY_METRIC.get(metric)
        if label is None:
            if metric not in unknown:
                unknown.append(metric)
        else:
            found.add(label)
    ordered = tuple(reason for reason in REASON_ORDER if reason in found)
    return ordered + tuple(unknown)


@dataclass(frozen=True, slots=True)
class AtRiskStudent:
    """One student behind one or more active flags the viewer is allowed to see."""

    student_id: UUID
    student_name: str
    reasons: tuple[str, ...]


async def list_at_risk_students(
    session: AsyncSession, scope: Scope, *, limit: int = 200
) -> list[AtRiskStudent]:
    """Distinct students from `list_flags` — the same rows the at-risk page shows.

    A teacher and an administrator therefore see the same students here as on
    their own flag list, including the Section 7.2 rule that an attendance-only
    flag (no subject offering) is visible to administrators only.
    """
    flags = await list_flags(session, scope, limit=limit)
    names: dict[UUID, str] = {}
    labels_by_student: dict[UUID, set[str]] = {}
    for flag in flags:
        names.setdefault(flag.student_id, flag.student_name)
        labels_by_student.setdefault(flag.student_id, set()).update(
            reasons_from_drivers(flag.drivers)
        )
    students = [
        AtRiskStudent(
            student_id=student_id,
            student_name=names[student_id],
            reasons=tuple(reason for reason in REASON_ORDER if reason in labels)
            + tuple(sorted(labels - set(REASON_ORDER))),
        )
        for student_id, labels in labels_by_student.items()
    ]
    students.sort(key=lambda student: (student.student_name.casefold(), str(student.student_id)))
    return students


async def list_flags(session: AsyncSession, scope: Scope, *, limit: int = 200) -> list[FlagRow]:
    """Active flags in scope. Students are blocked at the router (`require_role`)."""
    flags = (
        (
            await session.execute(
                select(AtRiskFlag)
                .where(flag_scope_predicate(scope), AtRiskFlag.status == AtRiskStatus.ACTIVE)
                .order_by(AtRiskFlag.tier.desc(), AtRiskFlag.computed_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    if not flags:
        return []

    student_ids = {flag.student_id for flag in flags}
    offering_ids = {
        flag.grade_subject_offering_id for flag in flags if flag.grade_subject_offering_id
    }

    names_by_student: dict[UUID, str] = {
        row[0]: row[1]
        for row in (
            await session.execute(
                select(StudentProfile.id, StudentProfile.full_name).where(
                    StudentProfile.id.in_(student_ids)
                )
            )
        ).all()
    }
    subjects_by_offering: dict[UUID, str] = {}
    if offering_ids:
        subjects_by_offering = {
            row[0]: row[1]
            for row in (
                await session.execute(
                    select(GradeSubjectOffering.id, Subject.name)
                    .join(Subject, Subject.id == GradeSubjectOffering.subject_id)
                    .where(GradeSubjectOffering.id.in_(offering_ids))
                )
            ).all()
        }

    return [
        FlagRow(
            id=flag.id,
            student_id=flag.student_id,
            student_name=names_by_student.get(flag.student_id, "Unknown"),
            grade_subject_offering_id=flag.grade_subject_offering_id,
            subject=(
                subjects_by_offering.get(flag.grade_subject_offering_id)
                if flag.grade_subject_offering_id
                else None
            ),
            tier=flag.tier,
            drivers=flag.drivers,
            status=flag.status,
            computed_at=flag.computed_at,
            dismissed_by_user_id=flag.dismissed_by_user_id,
            dismissed_at=flag.dismissed_at,
            dismissal_note=flag.dismissal_note,
        )
        for flag in flags
    ]


async def record_flag_view(
    session: AsyncSession, *, institution_id: UUID, actor_user_id: UUID, rows_returned: int
) -> None:
    await record_event(
        session,
        institution_id=institution_id,
        actor_user_id=actor_user_id,
        event_type=AuditAction.VIEW_FLAGS,
        entity_type="at_risk.flags",
        payload={"rows_returned": rows_returned},
    )


async def dismiss_flag(
    session: AsyncSession,
    scope: Scope,
    *,
    flag_id: UUID,
    actor_user_id: UUID,
    institution_id: UUID,
    note: str | None,
) -> FlagRow | None:
    """Dismiss in scope; return None if missing or out of scope (404 at router)."""
    statement = select(AtRiskFlag).where(
        AtRiskFlag.id == flag_id,
        flag_scope_predicate(scope),
        AtRiskFlag.status == AtRiskStatus.ACTIVE,
    )
    flag = (await session.execute(statement)).scalar_one_or_none()
    if flag is None:
        return None

    flag.status = AtRiskStatus.DISMISSED
    flag.dismissed_by_user_id = actor_user_id
    flag.dismissed_at = datetime.now(UTC)
    flag.dismissal_note = note
    await session.flush()

    await record_event(
        session,
        institution_id=institution_id,
        actor_user_id=actor_user_id,
        event_type=AuditAction.RECORD_INTERVENTION,
        entity_type="at_risk.flag",
        entity_id=flag.id,
        payload={"action": "dismiss", "student_id": str(flag.student_id)},
    )

    return FlagRow(
        id=flag.id,
        student_id=flag.student_id,
        student_name="",
        grade_subject_offering_id=flag.grade_subject_offering_id,
        subject=None,
        tier=flag.tier,
        drivers=flag.drivers,
        status=flag.status,
        computed_at=flag.computed_at,
        dismissed_by_user_id=flag.dismissed_by_user_id,
        dismissed_at=flag.dismissed_at,
        dismissal_note=flag.dismissal_note,
    )


async def _signals_for_institution(
    session: AsyncSession, institution_id: UUID
) -> list[StudentSignals]:
    """All student signals for recompute — not scope-filtered (admin-only at router)."""
    register = (
        (
            await session.execute(
                select(*STUDENT_360_SIGNAL_COLUMNS).where(
                    student_360.c.institution_id == institution_id
                )
            )
        )
        .mappings()
        .all()
    )

    enrolment_ids = [
        row.student_subject_enrollment_id
        for row in register
        if row.student_subject_enrollment_id is not None and row.quizzes_taken > 0
    ]
    scores_by_enrolment: dict[UUID, list[float]] = defaultdict(list)
    if enrolment_ids:
        attempt_rows = await session.execute(
            select(QuizAttempt.student_subject_enrollment_id, QuizAttempt.score_percent)
            .where(
                QuizAttempt.student_subject_enrollment_id.in_(enrolment_ids),
                QuizAttempt.status.in_(FINISHED_ATTEMPT_STATUSES),
                QuizAttempt.score_percent.is_not(None),
            )
            .order_by(QuizAttempt.student_subject_enrollment_id, QuizAttempt.submitted_at.desc())
        )
        for enrolment_id, score in attempt_rows.all():
            scores_by_enrolment[enrolment_id].append(float(score))

    @dataclass(slots=True)
    class _Bucket:
        attendance: float | None = None
        subjects: list[SubjectSignal] = field(default_factory=list)

    by_student: dict[UUID, _Bucket] = {}
    for row in register:
        bucket = by_student.setdefault(row.student_id, _Bucket())
        # student_360 coalesces "no scored attempts" to mastery_percent=0 (see the view's
        # COALESCE(avg_score_percent, 0)). Feeding that 0 into the mastery-level driver
        # falsely flags every enrolled subject a student has never sat a quiz for. Skip
        # subjects with no attempts -- same idea as attendance staying NULL when
        # days_counted is 0 -- so the engine only sees real mastery readings.
        if row.grade_subject_offering_id is not None and (row.quizzes_taken or 0) > 0:
            scores = scores_by_enrolment.get(row.student_subject_enrollment_id, [])
            bucket.subjects.append(
                SubjectSignal(
                    grade_subject_offering_id=row.grade_subject_offering_id,
                    mastery_percent=float(row.mastery_percent or 0.0),
                    attempt_scores_recent_first=tuple(scores),
                )
            )
        if row.attendance_percent is not None:
            bucket.attendance = float(row.attendance_percent)

    return [
        StudentSignals(
            student_id=student_id,
            attendance_percent=bucket.attendance,
            subjects=tuple(bucket.subjects),
        )
        for student_id, bucket in by_student.items()
    ]


@dataclass(frozen=True, slots=True)
class RecomputeResult:
    students_considered: int
    flags_active: int
    flags_resolved: int


async def recompute_institution(
    session: AsyncSession,
    *,
    institution_id: UUID,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> RecomputeResult:
    """Run engine for all students; upsert active flags, resolve stale ones."""
    signals = await _signals_for_institution(session, institution_id)

    academic_period_by_student: dict[UUID, UUID] = {}
    section_by_student: dict[UUID, UUID | None] = {}
    all_students = (
        await session.execute(
            select(*STUDENT_360_IDENTITY_COLUMNS).where(
                student_360.c.institution_id == institution_id
            )
        )
    ).all()
    for student_id, period_id, section_id in all_students:
        academic_period_by_student[student_id] = period_id
        section_by_student[student_id] = section_id

    now = datetime.now(UTC)
    new_keys: set[tuple[UUID, UUID | None]] = set()

    for student_signals in signals:
        flags: list[EngineFlag] = evaluate_student(student_signals, thresholds)
        for flag in flags:
            key = (student_signals.student_id, flag.grade_subject_offering_id)
            new_keys.add(key)
            await _upsert_flag(
                session,
                institution_id=institution_id,
                student_id=student_signals.student_id,
                academic_period_id=academic_period_by_student[student_signals.student_id],
                section_id=section_by_student.get(student_signals.student_id),
                flag=flag,
                computed_at=now,
            )

    resolved = await _resolve_stale_flags(session, institution_id=institution_id, keep=new_keys)
    await session.flush()

    return RecomputeResult(
        students_considered=len(signals),
        flags_active=len(new_keys),
        flags_resolved=resolved,
    )


async def _upsert_flag(
    session: AsyncSession,
    *,
    institution_id: UUID,
    student_id: UUID,
    academic_period_id: UUID,
    section_id: UUID | None,
    flag: EngineFlag,
    computed_at: datetime,
) -> None:
    drivers_payload = [
        {
            "metric": driver.metric,
            "value": driver.value,
            "comparison": driver.comparison,
            "window": driver.window,
        }
        for driver in flag.drivers
    ]

    existing = (
        await session.execute(
            select(AtRiskFlag).where(
                AtRiskFlag.student_id == student_id,
                AtRiskFlag.grade_subject_offering_id.is_(flag.grade_subject_offering_id)
                if flag.grade_subject_offering_id is None
                else AtRiskFlag.grade_subject_offering_id == flag.grade_subject_offering_id,
                AtRiskFlag.status == AtRiskStatus.ACTIVE,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.tier = AtRiskTier(flag.tier)
        existing.drivers = drivers_payload
        existing.computed_at = computed_at
        existing.section_id = section_id
        return

    session.add(
        AtRiskFlag(
            institution_id=institution_id,
            student_id=student_id,
            grade_subject_offering_id=flag.grade_subject_offering_id,
            section_id=section_id,
            academic_period_id=academic_period_id,
            tier=AtRiskTier(flag.tier),
            drivers=drivers_payload,
            status=AtRiskStatus.ACTIVE,
            computed_at=computed_at,
        )
    )


async def _resolve_stale_flags(
    session: AsyncSession, *, institution_id: UUID, keep: set[tuple[UUID, UUID | None]]
) -> int:
    active = (
        (
            await session.execute(
                select(AtRiskFlag).where(
                    AtRiskFlag.institution_id == institution_id,
                    AtRiskFlag.status == AtRiskStatus.ACTIVE,
                )
            )
        )
        .scalars()
        .all()
    )

    resolved_count = 0
    for row in active:
        if (row.student_id, row.grade_subject_offering_id) not in keep:
            row.status = AtRiskStatus.RESOLVED
            resolved_count += 1
    return resolved_count
