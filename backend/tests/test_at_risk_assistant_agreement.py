"""The assistant's struggling-students answer and the at-risk page share one set.

The old assistant SQL counted a student when mastery was below 60% or attendance
was below 80% on a subject row the teacher teaches. The page shows active
at-risk flags in the viewer's scope. Those differ in two ways, and on this
fixture the counts are 12 and 9:

* four students have attendance of 70% and mastery of 85%. The engine raises an
  attendance-only flag (no subject offering). Section 7.2 hides that flag from
  teachers because the scope predicate matches taught (offering, section) pairs,
  and a null offering matches none. The old SQL still counted them, because
  attendance is copied onto every subject row. Their section_id is set; the
  offering id is what drops them.
* one student has mastery of 78% and attendance of 90%, but the last three
  attempts are 20 points below the earlier ones. The engine flags the decline.
  The old SQL does not, because student_360 has no attempt series.

8 + 4 = 12 on the old query. 8 + 1 = 9 on the teacher's flag list. The four
attendance-only students are in the 12 and not the 9. The trend-only student is
in the 9 and not the 12.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from education_platform.db.url import to_sync_url
from education_platform.modules.academics.models import (
    AcademicPeriod,
    AcademicPeriodStatus,
    EnrollmentStatus,
    Grade,
    GradeSubjectOffering,
    PeriodGrade,
    Section,
    StudentGradeEnrollment,
    StudentSubjectEnrollment,
    Subject,
    TeachingAssignment,
    TeachingAssignmentStatus,
    Topic,
)
from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    QuizAttempt,
    QuizAttemptStatus,
    QuizScope,
    QuizVersion,
)
from education_platform.modules.at_risk.models import AtRiskFlag
from education_platform.modules.at_risk.service import reasons_from_drivers
from education_platform.modules.attendance.models import AttendanceRecord, AttendanceStatus
from education_platform.modules.auth.models import (
    Institution,
    RoleName,
    StudentProfile,
    User,
    UserRole,
)
from education_platform.modules.auth.security import hash_password
from education_platform.modules.text_to_sql.nodes.compose_answer import compose_answer
from education_platform.modules.text_to_sql.nodes.resolve_at_risk import resolve_at_risk
from education_platform.modules.text_to_sql.state import TextToSQLState

SCHOOL = "At Risk Agreement School"
PASSWORD = "demo1234"
ADMIN = "admin@agreement.school"
TEACHER = "teacher@agreement.school"

# The SQL the assistant used to run for "struggling students". Kept here so the
# regression still shows what that definition counts.
LEGACY_STRUGGLING_SQL = """
SELECT DISTINCT sp.full_name
FROM student_360 s
JOIN student_profiles sp ON sp.id = s.student_id
JOIN academic_periods ap ON ap.id = s.academic_period_id
WHERE (s.mastery_percent < 60.00 OR s.attendance_percent < 80.00)
  AND ap.status = 'active'
  AND (s.student_id, s.grade_subject_offering_id) IN (
    SELECT sse.student_id, sse.grade_subject_offering_id
    FROM student_subject_enrollments sse
    JOIN teaching_assignments ta
      ON ta.grade_subject_offering_id = sse.grade_subject_offering_id
    LEFT JOIN student_grade_enrollments sge ON sge.id = sse.grade_enrollment_id
    WHERE ta.teacher_user_id = :current_user_id
      AND ta.status = 'active'
      AND (ta.section_id IS NULL OR ta.section_id = sge.section_id)
  )
"""

CRITERION = (
    "using the at-risk definition for your classes, the same students as the at-risk "
    "flags. A flag is low mastery (below 60%), a mastery decline of more than 15 points, "
    "or low attendance (below 80%) when that flag is visible to you. Attendance-only "
    "flags are an administrator view and are not included for a teacher"
)


def test_driver_metrics_use_the_three_reason_labels() -> None:
    assert reasons_from_drivers(
        [
            {"metric": "mastery_trend", "value": 20},
            {"metric": "attendance_percent", "value": 70},
            {"metric": "mastery_percent", "value": 50},
        ]
    ) == ("low attendance", "low mastery", "declining trend")


@pytest.fixture()
def api(client: TestClient, clean_db: str) -> Iterator[TestClient]:
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        _seed(session)
        session.commit()
    engine.dispose()
    yield client


def _seed(session: Session) -> None:
    institution = Institution(name=SCHOOL, timezone="UTC")
    session.add(institution)
    session.flush()

    period = AcademicPeriod(
        institution_id=institution.id,
        name="Term 1",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
        status=AcademicPeriodStatus.ACTIVE,
    )
    grade = Grade(institution_id=institution.id, name="Grade 8")
    subject = Subject(institution_id=institution.id, name="Mathematics", code="MATH")
    session.add_all([period, grade, subject])
    session.flush()

    period_grade = PeriodGrade(academic_period_id=period.id, grade_id=grade.id)
    session.add(period_grade)
    session.flush()

    section = Section(period_grade_id=period_grade.id, name="8A")
    offering = GradeSubjectOffering(period_grade_id=period_grade.id, subject_id=subject.id)
    session.add_all([section, offering])
    session.flush()

    topic = Topic(
        grade_subject_offering_id=offering.id,
        name="Numbers",
        slug="numbers",
        sequence=1,
    )
    session.add(topic)
    session.flush()
    quiz = CommonMasteryQuiz(
        quiz_scope=QuizScope.TOPIC_MASTERY,
        topic_id=topic.id,
        title="Numbers topic mastery",
    )
    session.add(quiz)
    session.flush()
    version = QuizVersion(quiz_id=quiz.id, version_number=1)
    session.add(version)
    session.flush()

    password_hash = hash_password(PASSWORD)
    admin = User(
        institution_id=institution.id,
        email=ADMIN,
        full_name="Agreement Admin",
        password_hash=password_hash,
    )
    teacher = User(
        institution_id=institution.id,
        email=TEACHER,
        full_name="Agreement Teacher",
        password_hash=password_hash,
    )
    session.add_all([admin, teacher])
    session.flush()
    session.add_all(
        [
            UserRole(user_id=admin.id, role=RoleName.ADMINISTRATOR),
            UserRole(user_id=teacher.id, role=RoleName.TEACHER),
        ]
    )
    session.add(
        TeachingAssignment(
            teacher_user_id=teacher.id,
            academic_period_id=period.id,
            grade_subject_offering_id=offering.id,
            section_id=section.id,
            status=TeachingAssignmentStatus.ACTIVE,
        )
    )

    # 8 low mastery, 1 trend-only, 4 attendance-only.
    for index in range(1, 9):
        _enroll(
            session,
            institution_id=institution.id,
            period_id=period.id,
            period_grade_id=period_grade.id,
            section_id=section.id,
            offering_id=offering.id,
            version_id=version.id,
            name=f"Low Mastery {index}",
            identifier=f"LM-{index}",
            scores=(50.0,),
            days_present=9,
        )
    _enroll(
        session,
        institution_id=institution.id,
        period_id=period.id,
        period_grade_id=period_grade.id,
        section_id=section.id,
        offering_id=offering.id,
        version_id=version.id,
        name="Trend Only",
        identifier="TR-1",
        # Recent-first once ordered by submitted_at desc: 70, 70, 70, 90, 90.
        scores=(90.0, 90.0, 70.0, 70.0, 70.0),
        days_present=9,
    )
    for index in range(1, 5):
        _enroll(
            session,
            institution_id=institution.id,
            period_id=period.id,
            period_grade_id=period_grade.id,
            section_id=section.id,
            offering_id=offering.id,
            version_id=version.id,
            name=f"Attendance Only {index}",
            identifier=f"AT-{index}",
            scores=(85.0,),
            days_present=7,
        )


def _enroll(
    session: Session,
    *,
    institution_id: object,
    period_id: object,
    period_grade_id: object,
    section_id: object,
    offering_id: object,
    version_id: object,
    name: str,
    identifier: str,
    scores: tuple[float, ...],
    days_present: int,
) -> None:
    profile = StudentProfile(
        institution_id=institution_id,
        student_identifier=identifier,
        full_name=name,
    )
    session.add(profile)
    session.flush()
    grade_enrollment = StudentGradeEnrollment(
        student_id=profile.id,
        academic_period_id=period_id,
        period_grade_id=period_grade_id,
        section_id=section_id,
        status=EnrollmentStatus.ACTIVE,
    )
    session.add(grade_enrollment)
    session.flush()
    subject_enrollment = StudentSubjectEnrollment(
        student_id=profile.id,
        grade_enrollment_id=grade_enrollment.id,
        grade_subject_offering_id=offering_id,
        status=EnrollmentStatus.ACTIVE,
    )
    session.add(subject_enrollment)
    session.flush()

    now = datetime(2026, 6, 1, tzinfo=UTC)
    for attempt_number, score in enumerate(scores, start=1):
        # Earlier scores are submitted earlier, so the engine's recent-first
        # window sees the end of `scores` first.
        submitted = now - timedelta(days=len(scores) - attempt_number)
        session.add(
            QuizAttempt(
                student_id=profile.id,
                student_subject_enrollment_id=subject_enrollment.id,
                quiz_version_id=version_id,
                attempt_number=attempt_number,
                status=QuizAttemptStatus.RELEASED,
                started_at=submitted - timedelta(minutes=20),
                submitted_at=submitted,
                scored_at=submitted,
                score_percent=Decimal(str(score)),
                score_raw=Decimal(str(score)),
                pass_threshold_percent=Decimal("70.00"),
                passed=score >= 70,
            )
        )

    start = date(2026, 1, 5)
    for day in range(10):
        session.add(
            AttendanceRecord(
                student_id=profile.id,
                academic_period_id=period_id,
                section_id=section_id,
                on_date=start + timedelta(days=day),
                status=AttendanceStatus.PRESENT if day < days_present else AttendanceStatus.ABSENT,
            )
        )


def _headers(api: TestClient, email: str) -> dict[str, str]:
    response = api.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD, "institution_name": SCHOOL},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _legacy_names(database_url: str, teacher_user_id: str) -> set[str]:
    engine = create_engine(to_sync_url(database_url), pool_pre_ping=True)
    with engine.connect() as connection:
        rows = connection.execute(
            text(LEGACY_STRUGGLING_SQL), {"current_user_id": teacher_user_id}
        ).all()
    engine.dispose()
    return {row[0] for row in rows}


def _teacher_user_id(database_url: str) -> str:
    engine = create_engine(to_sync_url(database_url), pool_pre_ping=True)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT id FROM users WHERE email = :email"), {"email": TEACHER}
        ).one()
    engine.dispose()
    return str(row[0])


@pytest.mark.asyncio
async def test_legacy_sql_counts_12_and_the_teacher_page_counts_9(
    api: TestClient, clean_db: str
) -> None:
    """The discrepancy, before the assistant is pointed at the flag service."""
    admin_headers = _headers(api, ADMIN)
    recompute = api.post("/api/v1/at-risk/recompute", headers=admin_headers)
    assert recompute.status_code == 200, recompute.text

    teacher_headers = _headers(api, TEACHER)
    teacher_flags = api.get("/api/v1/at-risk/flags", headers=teacher_headers).json()["items"]
    admin_flags = api.get("/api/v1/at-risk/flags", headers=admin_headers).json()["items"]

    teacher_names = {flag["student_name"] for flag in teacher_flags}
    low_mastery = {f"Low Mastery {index}" for index in range(1, 9)}
    attendance_only = {f"Attendance Only {index}" for index in range(1, 5)}
    assert teacher_names == low_mastery | {"Trend Only"}
    assert len(teacher_names) == 9
    assert attendance_only.isdisjoint(teacher_names)
    assert all(flag["subject"] == "Mathematics" for flag in teacher_flags)

    legacy = _legacy_names(clean_db, _teacher_user_id(clean_db))
    assert len(legacy) == 12
    assert legacy - teacher_names == attendance_only
    assert teacher_names - legacy == {"Trend Only"}

    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        rows = session.scalars(
            select(AtRiskFlag).where(AtRiskFlag.grade_subject_offering_id.is_(None))
        ).all()
    engine.dispose()
    assert len(rows) == 4
    assert all(row.section_id is not None for row in rows)
    assert {flag["student_name"] for flag in admin_flags if flag["subject"] is None} == (
        attendance_only
    )


@pytest.mark.asyncio
async def test_assistant_and_flag_endpoint_return_the_same_students(
    api: TestClient, clean_db: str
) -> None:
    admin_headers = _headers(api, ADMIN)
    assert api.post("/api/v1/at-risk/recompute", headers=admin_headers).status_code == 200
    teacher_headers = _headers(api, TEACHER)
    teacher_flags = api.get("/api/v1/at-risk/flags", headers=teacher_headers).json()["items"]
    admin_flags = api.get("/api/v1/at-risk/flags", headers=admin_headers).json()["items"]
    teacher_id = _teacher_user_id(clean_db)

    listed = await resolve_at_risk(_ask_state(clean_db, teacher_id, role="teacher", shape="list"))
    counted = await resolve_at_risk(_ask_state(clean_db, teacher_id, role="teacher", shape="count"))
    assert listed["error"] is None
    tool_names = {row["full_name"] for row in listed["query_result"]}
    endpoint_names = {flag["student_name"] for flag in teacher_flags}
    assert tool_names == endpoint_names
    assert counted["query_result"] == [{"student_count": len(endpoint_names)}]
    assert counted["query_result"][0]["student_count"] == 9

    reasons = {row["full_name"]: row["reasons"] for row in listed["query_result"]}
    assert reasons["Trend Only"] == "declining trend"
    assert reasons["Low Mastery 1"] == "low mastery"
    assert "Attendance Only 1" not in reasons

    admin_id = _user_id(clean_db, ADMIN)
    admin_listed = await resolve_at_risk(
        _ask_state(clean_db, admin_id, role="administrator", shape="list")
    )
    admin_tool = {row["full_name"] for row in admin_listed["query_result"]}
    admin_endpoint = {flag["student_name"] for flag in admin_flags}
    assert admin_tool == admin_endpoint
    assert {
        row["reasons"]
        for row in admin_listed["query_result"]
        if row["full_name"].startswith("Attendance")
    } == {"low attendance"}

    composed = await compose_answer(
        {
            **listed,
            "question": "Show me the struggling students.",
            "answer_criterion": CRITERION,
            "confidence": "high",
            "query_source": "service",
        }
    )
    answer = composed["natural_answer"]
    assert "at-risk definition" in answer
    for name in endpoint_names:
        assert name in answer
    assert "Attendance Only 1" not in answer
    assert "same students as the at-risk flags" in answer


def _user_id(database_url: str, email: str) -> str:
    engine = create_engine(to_sync_url(database_url), pool_pre_ping=True)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT id FROM users WHERE email = :email"), {"email": email}
        ).one()
    engine.dispose()
    return str(row[0])


def _ask_state(database_url: str, user_id: str, *, role: str, shape: str) -> TextToSQLState:
    engine = create_engine(to_sync_url(database_url), pool_pre_ping=True)
    with engine.connect() as connection:
        institution_id = connection.execute(
            text("SELECT id FROM institutions WHERE name = :name"), {"name": SCHOOL}
        ).scalar_one()
    engine.dispose()
    return {
        "question": "Show me the struggling students.",
        "user_id": user_id,
        "user_role": role,
        "institution_id": str(institution_id),
        "intent_parameters": {"shape": shape},
        "query_source": "service",
        "error": None,
        "audit_entry": None,
    }
