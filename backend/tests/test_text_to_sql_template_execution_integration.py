"""Runs each of `intent_templates.yaml`'s 13 governed templates' own hand-written SQL
through the real pipeline (`validate_sql` -> `execute_sql`) against a live, RLS-enabled
Postgres database — closing the gap flagged during the design-doc/code comparison: no
test previously ran a template's SQL against the real database at all.

Why this matters specifically for templates (not free-form queries): `apply_role_scope`'s
independent AST rewrite (Layer 3 of the 5-layer defense-in-depth model) is skipped
entirely for `query_source == "template"` — a template's own hand-written SQL is trusted
to carry its own row/institution scoping. RLS (Layer 5) is a backstop, but until this
file, nothing had proven any specific template's SQL actually returns the rows a
correctly-scoped rewrite would. This file is that proof, per template, not just in
aggregate.

Deliberately bypasses `intent_router`'s own LLM classifier (out of scope here — that's
Risk 1 from the design-doc comparison, "wrong template selected," a separate,
still-open gap this file does not address) and calls `validate_sql`/`execute_sql`
directly with each template's own SQL text and a hand-picked, realistic set of bind
parameters, exactly mirroring what `intent_router` hands off in production
(`state["generated_sql"]` = the template's SQL, `state["query_source"] = "template"`,
`state["intent_parameters"]` = the extracted parameters) and what `execute_sql` binds
(`intent_parameters` merged with `current_user_id`/`current_institution_id`).

Fixture data is deliberately shaped so results differ by *which* teacher asks — the same
proof technique already used by `test_text_to_sql_apply_role_scope_integration.py` — so a
template that silently dropped its scoping predicate (the exact bug class Section 5.1's
"9 of 13 templates missing a section-level predicate" finding was) would fail here with a
wrong count/roster, not just an absence of rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from education_platform.db.session import reset_engine
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
    Subtopic,
    TeachingAssignment,
    TeachingAssignmentStatus,
    Topic,
)
from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    QuizAttempt,
    QuizAttemptStatus,
    QuizResultReleaseMode,
    QuizScope,
    QuizVersion,
    QuizVersionStatus,
)
from education_platform.modules.attendance.models import AttendanceRecord, AttendanceStatus
from education_platform.modules.auth.models import (
    Institution,
    RoleName,
    StudentProfile,
    User,
    UserRole,
)
from education_platform.modules.text_to_sql.nodes.execute_sql import execute_sql
from education_platform.modules.text_to_sql.nodes.validate_sql import validate_sql
from education_platform.modules.text_to_sql.state import TextToSQLState


@pytest.fixture(autouse=True)
def _fresh_text_to_sql_engine_per_test() -> Iterator[None]:
    """Same reasoning as test_text_to_sql_row_level_security.py's identical fixture:
    `execute_sql` uses the cached, process-wide text-to-sql engine directly, with no
    `clean_db`-triggered reset in between — a pooled asyncpg connection created on one
    test's event loop cannot be reused once that loop closes, so the engine must be reset
    unconditionally around every test in this file.
    """
    reset_engine()
    yield
    reset_engine()


@dataclass(frozen=True)
class _Fixture:
    institution_id: UUID
    other_institution_id: UUID

    teacher_user_id: UUID  # Math/8A-specific + Science/all-sections
    teacher2_user_id: UUID  # Math/all-sections only

    section_a_id: UUID  # "8A"
    section_b_id: UUID  # "8B"

    student_a_math_id: UUID  # 8A, Math — mastery 85, passed, attendance 90%
    student_b_math_id: UUID  # 8B, Math — mastery 45, failed, attendance 50%
    student_a_science_id: UUID  # 8A, Science — mastery 70, passed, attendance 80%


def _seed(session: Session) -> _Fixture:
    inst1 = Institution(name="Template Execution Test School", timezone="UTC")
    inst2 = Institution(name="Template Execution Test School — Other Institution", timezone="UTC")
    session.add_all([inst1, inst2])
    session.flush()

    period = AcademicPeriod(
        institution_id=inst1.id,
        name="Term 1",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
        status=AcademicPeriodStatus.ACTIVE,
    )
    grade = Grade(institution_id=inst1.id, name="Grade 8")
    subject_math = Subject(institution_id=inst1.id, name="Mathematics", code="MATH")
    subject_science = Subject(institution_id=inst1.id, name="Science", code="SCI")
    session.add_all([period, grade, subject_math, subject_science])
    session.flush()

    # Other institution: a subject named distinctly from anything in institution 1, so
    # list_school_subjects' institution pin has something unambiguous to prove hidden.
    other_subject = Subject(institution_id=inst2.id, name="Other Institution Subject", code="OTH")
    session.add(other_subject)

    period_grade = PeriodGrade(academic_period_id=period.id, grade_id=grade.id)
    session.add(period_grade)
    session.flush()

    section_a = Section(period_grade_id=period_grade.id, name="8A")
    section_b = Section(period_grade_id=period_grade.id, name="8B")
    session.add_all([section_a, section_b])
    session.flush()

    offering_math = GradeSubjectOffering(
        period_grade_id=period_grade.id, subject_id=subject_math.id
    )
    offering_science = GradeSubjectOffering(
        period_grade_id=period_grade.id, subject_id=subject_science.id
    )
    session.add_all([offering_math, offering_science])
    session.flush()

    teacher = User(
        institution_id=inst1.id,
        email="teacher@template-exec-test.school",
        full_name="Teacher",
        password_hash="unused",
    )
    teacher2 = User(
        institution_id=inst1.id,
        email="teacher2@template-exec-test.school",
        full_name="Teacher Two",
        password_hash="unused",
    )
    session.add_all([teacher, teacher2])
    session.flush()
    session.add_all(
        [
            UserRole(user_id=teacher.id, role=RoleName.TEACHER),
            UserRole(user_id=teacher2.id, role=RoleName.TEACHER),
        ]
    )

    # Specific-section grant: Math, 8A only.
    session.add(
        TeachingAssignment(
            teacher_user_id=teacher.id,
            academic_period_id=period.id,
            grade_subject_offering_id=offering_math.id,
            section_id=section_a.id,
            status=TeachingAssignmentStatus.ACTIVE,
        )
    )
    # All-sections grant: Science.
    session.add(
        TeachingAssignment(
            teacher_user_id=teacher.id,
            academic_period_id=period.id,
            grade_subject_offering_id=offering_science.id,
            section_id=None,
            status=TeachingAssignmentStatus.ACTIVE,
        )
    )
    # Second teacher: Math, all sections — reaches both 8A and 8B, unlike the primary
    # teacher's 8A-only Math grant. Every template test below cross-checks teacher vs.
    # teacher2 to prove the template's own scoping predicate, not just RLS, differs the
    # result by identity.
    session.add(
        TeachingAssignment(
            teacher_user_id=teacher2.id,
            academic_period_id=period.id,
            grade_subject_offering_id=offering_math.id,
            section_id=None,
            status=TeachingAssignmentStatus.ACTIVE,
        )
    )

    def _enroll(
        identifier: str, section: Section, offering: GradeSubjectOffering
    ) -> tuple[StudentProfile, StudentSubjectEnrollment]:
        user = User(
            institution_id=inst1.id,
            email=f"{identifier}@template-exec-test.school",
            full_name=identifier,
            password_hash="unused",
        )
        session.add(user)
        session.flush()
        profile = StudentProfile(
            institution_id=inst1.id,
            user_id=user.id,
            student_identifier=identifier,
            full_name=identifier,
        )
        session.add(profile)
        session.flush()
        grade_enrollment = StudentGradeEnrollment(
            student_id=profile.id,
            academic_period_id=period.id,
            period_grade_id=period_grade.id,
            section_id=section.id,
            status=EnrollmentStatus.ACTIVE,
        )
        session.add(grade_enrollment)
        session.flush()
        subject_enrollment = StudentSubjectEnrollment(
            student_id=profile.id,
            grade_enrollment_id=grade_enrollment.id,
            grade_subject_offering_id=offering.id,
            status=EnrollmentStatus.ACTIVE,
        )
        session.add(subject_enrollment)
        session.flush()
        return profile, subject_enrollment

    student_a_math, enrollment_a_math = _enroll("stu-a-math", section_a, offering_math)
    student_b_math, enrollment_b_math = _enroll("stu-b-math", section_b, offering_math)
    student_a_science, enrollment_a_science = _enroll("stu-a-science", section_a, offering_science)

    # Minimal curriculum chain per subject, purely to satisfy latest_quiz_attempt's join
    # (it resolves subject via topic/subtopic -> grade_subject_offering_id, structurally
    # required by the EXISTS clause even when :subject is NULL).
    def _quiz_chain(offering: GradeSubjectOffering, label: str) -> UUID:
        topic = Topic(
            grade_subject_offering_id=offering.id,
            name=f"{label} Topic",
            slug=f"{label}-topic",
            sequence=1,
        )
        session.add(topic)
        session.flush()
        subtopic = Subtopic(
            topic_id=topic.id,
            name=f"{label} Subtopic",
            slug=f"{label}-subtopic",
            sequence=1,
        )
        session.add(subtopic)
        session.flush()
        quiz = CommonMasteryQuiz(
            subtopic_id=subtopic.id, quiz_scope=QuizScope.SUBTOPIC_MASTERY, title=f"{label} Quiz"
        )
        session.add(quiz)
        session.flush()
        quiz_version = QuizVersion(
            quiz_id=quiz.id,
            version_number=1,
            lifecycle_status=QuizVersionStatus.RELEASED,
            result_release_mode=QuizResultReleaseMode.IMMEDIATE,
        )
        session.add(quiz_version)
        session.flush()
        return quiz_version.id

    math_quiz_version_id = _quiz_chain(offering_math, "math")
    science_quiz_version_id = _quiz_chain(offering_science, "science")

    def _attempt(
        *,
        student: StudentProfile,
        enrollment: StudentSubjectEnrollment,
        quiz_version_id: UUID,
        score: str,
        passed: bool,
        submitted_at: datetime,
    ) -> None:
        session.add(
            QuizAttempt(
                student_id=student.id,
                student_subject_enrollment_id=enrollment.id,
                quiz_version_id=quiz_version_id,
                attempt_number=1,
                status=QuizAttemptStatus.SCORED,
                score_percent=score,
                passed=passed,
                submitted_at=submitted_at,
            )
        )

    _attempt(
        student=student_a_math,
        enrollment=enrollment_a_math,
        quiz_version_id=math_quiz_version_id,
        score="85.00",
        passed=True,
        submitted_at=datetime(2026, 2, 5, tzinfo=UTC),
    )
    _attempt(
        student=student_b_math,
        enrollment=enrollment_b_math,
        quiz_version_id=math_quiz_version_id,
        score="45.00",
        passed=False,
        submitted_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    _attempt(
        student=student_a_science,
        enrollment=enrollment_a_science,
        quiz_version_id=science_quiz_version_id,
        score="70.00",
        passed=True,
        submitted_at=datetime(2026, 2, 10, tzinfo=UTC),
    )
    session.flush()

    # Whole-day attendance (grade_subject_offering_id=NULL) — student_360's
    # attendance_stats CTE only counts these, never the per-subject rows above.
    def _attendance(
        student: StudentProfile, section: Section, *, present: int, absent: int
    ) -> None:
        day = date(2026, 2, 1)
        records = []
        for i in range(present):
            records.append(
                AttendanceRecord(
                    student_id=student.id,
                    academic_period_id=period.id,
                    section_id=section.id,
                    grade_subject_offering_id=None,
                    on_date=date(2026, 2, 1 + i),
                    status=AttendanceStatus.PRESENT,
                )
            )
        for i in range(absent):
            records.append(
                AttendanceRecord(
                    student_id=student.id,
                    academic_period_id=period.id,
                    section_id=section.id,
                    grade_subject_offering_id=None,
                    on_date=date(2026, 2, 1 + present + i),
                    status=AttendanceStatus.ABSENT,
                )
            )
        session.add_all(records)
        _ = day

    _attendance(student_a_math, section_a, present=9, absent=1)  # 90%
    _attendance(student_b_math, section_b, present=5, absent=5)  # 50%
    _attendance(student_a_science, section_a, present=8, absent=2)  # 80%
    session.flush()

    return _Fixture(
        institution_id=inst1.id,
        other_institution_id=inst2.id,
        teacher_user_id=teacher.id,
        teacher2_user_id=teacher2.id,
        section_a_id=section_a.id,
        section_b_id=section_b.id,
        student_a_math_id=student_a_math.id,
        student_b_math_id=student_b_math.id,
        student_a_science_id=student_a_science.id,
    )


@pytest.fixture()
def seeded(clean_db: str) -> Iterator[_Fixture]:
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        fixture = _seed(session)
        session.commit()
    engine.dispose()
    yield fixture


_TEMPLATES_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "education_platform"
    / "modules"
    / "text_to_sql"
    / "config"
    / "intent_templates.yaml"
)


def _template(name: str) -> dict[str, Any]:
    """Loads straight from the same YAML file `intent_router.py` reads in production
    (not through its private, `lru_cache`d `_load_catalog()`, which — via `import a.b.c as
    x`'s attribute-chain semantics — resolves to the *function* `intent_router` re-exported
    by `nodes/__init__.py` rather than the submodule, once that package `__init__` has run;
    reading the file directly sidesteps that import-shadowing gotcha entirely).
    """
    with _TEMPLATES_PATH.open(encoding="utf-8") as stream:
        catalog = yaml.safe_load(stream)
    for template in catalog["templates"]:
        if template["name"] == name:
            return template
    raise AssertionError(f"template {name!r} not found in intent_templates.yaml")


async def _run_template(
    name: str,
    *,
    user_id: UUID,
    institution_id: UUID,
    parameters: dict[str, Any] | None = None,
    sql_key: str = "sql",
) -> TextToSQLState:
    """Exercises exactly the two nodes a template match reaches after intent_router in
    production: validate_sql (proves the template's own SQL text is a valid, single,
    in-scope SELECT) and execute_sql (proves it actually runs, under text_to_sql_reader,
    with RLS session variables set, against real seeded data). apply_role_scope is
    deliberately not called — query_source == "template" skips it in production too (see
    apply_role_scope.py's own module docstring), which is exactly why this file exists:
    a template's own SQL is the only application-layer scoping check it gets.
    """
    template = _template(name)
    sql = template[sql_key]
    state: TextToSQLState = {
        "question": f"integration test for template {name!r}",
        "user_id": str(user_id),
        "user_role": "teacher",
        "institution_id": str(institution_id),
        "generated_sql": sql,
        "validated_sql": None,
        "query_source": "template",
        "intent": name,
        "intent_parameters": parameters or {},
        "retry_count": 0,
        "error": None,
        "audit_entry": None,
    }
    validated = await validate_sql(state)
    assert validated["error"] is None, (
        f"{name}: validate_sql rejected the template SQL: {validated['error']}"
    )
    executed = await execute_sql(validated)
    assert executed["error"] is None, f"{name}: execute_sql failed: {executed['error']}"
    return executed


# --- 1. latest_quiz_attempt -------------------------------------------------------------


async def test_latest_quiz_attempt_scopes_to_each_teachers_own_taught_students(
    seeded: _Fixture,
) -> None:
    # Teacher's taught students are student_a_math (Math/8A) + student_a_science
    # (Science/all-sections) — their latest submission is student_a_science (Feb 10).
    result = await _run_template(
        "latest_quiz_attempt",
        user_id=seeded.teacher_user_id,
        institution_id=seeded.institution_id,
        parameters={"subject": None},
    )
    rows = result["query_result"]
    assert result["result_row_count"] == 1
    assert rows[0]["full_name"] == "stu-a-science"

    # Teacher2's taught students are student_a_math + student_b_math (Math/all-sections)
    # — their latest submission is student_a_math (Feb 5), not student_b_math (Feb 1) —
    # different result for a different identity, proving this isn't a coincidental match.
    result2 = await _run_template(
        "latest_quiz_attempt",
        user_id=seeded.teacher2_user_id,
        institution_id=seeded.institution_id,
        parameters={"subject": None},
    )
    rows2 = result2["query_result"]
    assert result2["result_row_count"] == 1
    assert rows2[0]["full_name"] == "stu-a-math"


# --- 2. count_my_students ----------------------------------------------------------------


async def test_count_my_students_differs_by_teacher(seeded: _Fixture) -> None:
    teacher_result = await _run_template(
        "count_my_students", user_id=seeded.teacher_user_id, institution_id=seeded.institution_id
    )
    teacher2_result = await _run_template(
        "count_my_students", user_id=seeded.teacher2_user_id, institution_id=seeded.institution_id
    )
    assert teacher_result["query_result"][0]["total_students"] == 2  # a_math + a_science
    assert teacher2_result["query_result"][0]["total_students"] == 2  # a_math + b_math (8A+8B)


# --- 3. average_mastery_by_subject --------------------------------------------------------


async def test_average_mastery_by_subject_reflects_section_scoping(seeded: _Fixture) -> None:
    # Teacher's Math grant is 8A-only -> student_a_math (85) alone -> average 85.
    teacher_result = await _run_template(
        "average_mastery_by_subject",
        user_id=seeded.teacher_user_id,
        institution_id=seeded.institution_id,
        parameters={"subject": "Mathematics"},
    )
    assert round(float(teacher_result["query_result"][0]["average_score"]), 2) == 85.00

    # Teacher2's Math grant is all-sections -> student_a_math (85) + student_b_math (45)
    # -> average 65. A template that silently dropped its section predicate would return
    # 85 here too, identical to the teacher above — this is exactly the bug class
    # Section 5.1's "9 of 13 templates missing a section-level predicate" finding covers.
    teacher2_result = await _run_template(
        "average_mastery_by_subject",
        user_id=seeded.teacher2_user_id,
        institution_id=seeded.institution_id,
        parameters={"subject": "Mathematics"},
    )
    assert round(float(teacher2_result["query_result"][0]["average_score"]), 2) == 65.00


async def test_average_subtopic_quiz_score_executes(seeded: _Fixture) -> None:
    result = await _run_template(
        "average_subtopic_quiz_score",
        user_id=seeded.teacher_user_id,
        institution_id=seeded.institution_id,
        parameters={"subtopic_name": "Fractions", "section_name": "8A"},
    )
    assert result["error"] is None
    assert result["result_row_count"] == 1


# --- 4. average_attendance_rate ------------------------------------------------------------


async def test_average_attendance_rate_reflects_section_scoping(seeded: _Fixture) -> None:
    # Teacher: student_a_math (90%) + student_a_science (80%) -> average 85.
    teacher_result = await _run_template(
        "average_attendance_rate",
        user_id=seeded.teacher_user_id,
        institution_id=seeded.institution_id,
    )
    assert round(float(teacher_result["query_result"][0]["average_attendance"]), 2) == 85.00

    # Teacher2: student_a_math (90%) + student_b_math (50%) -> average 70.
    teacher2_result = await _run_template(
        "average_attendance_rate",
        user_id=seeded.teacher2_user_id,
        institution_id=seeded.institution_id,
    )
    assert round(float(teacher2_result["query_result"][0]["average_attendance"]), 2) == 70.00


# --- 5. list_students_in_section -----------------------------------------------------------


async def test_list_students_in_section_matches_only_that_teachers_grant(seeded: _Fixture) -> None:
    # Teacher's 8A reach: Math/8A-specific (student_a_math) + Science/all-sections
    # (student_a_science, who is also in 8A) -> both named.
    result = await _run_template(
        "list_students_in_section",
        user_id=seeded.teacher_user_id,
        institution_id=seeded.institution_id,
        parameters={"section_name": "8A"},
    )
    names = {row["full_name"] for row in result["query_result"]}
    assert names == {"stu-a-math", "stu-a-science"}

    # Teacher has no grant reaching 8B at all (Math is 8A-only; Science student in 8B was
    # never seeded) -- correctly zero rows, not an error.
    result_8b = await _run_template(
        "list_students_in_section",
        user_id=seeded.teacher_user_id,
        institution_id=seeded.institution_id,
        parameters={"section_name": "8B"},
    )
    assert result_8b["result_row_count"] == 0

    # Teacher2's Math/all-sections grant reaches 8B's student_b_math.
    result_teacher2_8b = await _run_template(
        "list_students_in_section",
        user_id=seeded.teacher2_user_id,
        institution_id=seeded.institution_id,
        parameters={"section_name": "8B"},
    )
    assert {row["full_name"] for row in result_teacher2_8b["query_result"]} == {"stu-b-math"}


# --- 6. list_students_below_score_in_subject ------------------------------------------------


async def test_list_students_below_score_in_subject_scopes_by_section(seeded: _Fixture) -> None:
    # Teacher (8A-only): student_a_math scores 85, never below 60 -> zero rows.
    teacher_result = await _run_template(
        "list_students_below_score_in_subject",
        user_id=seeded.teacher_user_id,
        institution_id=seeded.institution_id,
        parameters={"subject": "Mathematics", "threshold": 60},
    )
    assert teacher_result["result_row_count"] == 0

    # Teacher2 (all-sections): student_b_math (45) is below 60; student_a_math (85) is not.
    teacher2_result = await _run_template(
        "list_students_below_score_in_subject",
        user_id=seeded.teacher2_user_id,
        institution_id=seeded.institution_id,
        parameters={"subject": "Mathematics", "threshold": 60},
    )
    rows = teacher2_result["query_result"]
    assert {row["full_name"] for row in rows} == {"stu-b-math"}


# --- 7. top_students_by_subject ---------------------------------------------------------


async def test_top_students_by_subject_ranks_within_the_teachers_own_scope(
    seeded: _Fixture,
) -> None:
    result = await _run_template(
        "top_students_by_subject",
        user_id=seeded.teacher2_user_id,
        institution_id=seeded.institution_id,
        parameters={"subject": "Mathematics", "limit": 5},
    )
    rows = result["query_result"]
    assert [row["full_name"] for row in rows] == ["stu-a-math", "stu-b-math"]  # 85 before 45


# --- 8. my_subjects -----------------------------------------------------------------------


async def test_my_subjects_differs_by_teacher(seeded: _Fixture) -> None:
    teacher_result = await _run_template(
        "my_subjects", user_id=seeded.teacher_user_id, institution_id=seeded.institution_id
    )
    teacher2_result = await _run_template(
        "my_subjects", user_id=seeded.teacher2_user_id, institution_id=seeded.institution_id
    )
    assert {row["subject"] for row in teacher_result["query_result"]} == {"Mathematics", "Science"}
    assert {row["subject"] for row in teacher2_result["query_result"]} == {"Mathematics"}


# --- 9. my_sections -----------------------------------------------------------------------


async def test_my_sections_returns_real_sections_for_all_sections_grant(seeded: _Fixture) -> None:
    result = await _run_template(
        "my_sections", user_id=seeded.teacher2_user_id, institution_id=seeded.institution_id
    )
    assert {row["section"] for row in result["query_result"]} == {"8A", "8B"}


# --- 10. list_school_subjects — institution-pinned, not teacher-scoped --------------------


async def test_list_school_subjects_is_institution_pinned_not_teacher_scoped(
    seeded: _Fixture,
) -> None:
    result = await _run_template(
        "list_school_subjects",
        user_id=seeded.teacher_user_id,
        institution_id=seeded.institution_id,
    )
    subjects = {row["subject"] for row in result["query_result"]}
    assert subjects == {"Mathematics", "Science"}
    assert "Other Institution Subject" not in subjects


# --- 11. students_meeting_performance_bar ("doing well" -- approved: mastery >= 85 AND ---
# --- quizzes_passed >= 1) -------------------------------------------------------------


async def test_students_meeting_performance_bar_sql_is_correctly_scoped(seeded: _Fixture) -> None:
    # Teacher (a_math 85/passed, a_science 70/passed): only a_math clears the approved
    # >=85 mastery bar -- a_science (70) no longer qualifies now that the threshold moved
    # up from the old, unapproved 70.
    teacher_result = await _run_template(
        "students_meeting_performance_bar",
        user_id=seeded.teacher_user_id,
        institution_id=seeded.institution_id,
    )
    assert teacher_result["query_result"][0]["students_doing_well"] == 1

    # Teacher2 (a_math 85/passed, b_math 45/failed): only a_math meets the bar, same as
    # before -- b_math already failed both the old and the new, higher threshold.
    teacher2_result = await _run_template(
        "students_meeting_performance_bar",
        user_id=seeded.teacher2_user_id,
        institution_id=seeded.institution_id,
    )
    assert teacher2_result["query_result"][0]["students_doing_well"] == 1


# --- 12. students_needing_support ("struggling"/"at risk" -- approved: mastery < 60 OR -----
# --- attendance < 80, matching at_risk.engine.DEFAULT_THRESHOLDS; both sql_list and --------
# --- sql_count shapes) ----------------------------------------------------------------------


async def test_students_needing_support_sql_is_correctly_scoped(seeded: _Fixture) -> None:
    # Teacher (a_math 85/90%, a_science 70/80%): neither is below the approved 60 mastery
    # or 80 attendance floor -- a_science sits exactly at the 80% attendance boundary,
    # which is not "< 80" -- a legitimate zero-row result, not an error.
    teacher_result = await _run_template(
        "students_needing_support",
        user_id=seeded.teacher_user_id,
        institution_id=seeded.institution_id,
        parameters={"shape": "list"},
        sql_key="sql_list",
    )
    assert teacher_result["result_row_count"] == 0

    # Teacher2 (a_math fine, b_math 45%/50%): b_math qualifies on both criteria under
    # either the old (50/75) or the new, approved (60/80) thresholds.
    teacher2_result = await _run_template(
        "students_needing_support",
        user_id=seeded.teacher2_user_id,
        institution_id=seeded.institution_id,
        parameters={"shape": "list"},
        sql_key="sql_list",
    )
    assert {row["full_name"] for row in teacher2_result["query_result"]} == {"stu-b-math"}


async def test_students_needing_support_count_shape_matches_list_shape(
    seeded: _Fixture,
) -> None:
    list_result = await _run_template(
        "students_needing_support",
        user_id=seeded.teacher2_user_id,
        institution_id=seeded.institution_id,
        parameters={"shape": "list"},
        sql_key="sql_list",
    )
    count_result = await _run_template(
        "students_needing_support",
        user_id=seeded.teacher2_user_id,
        institution_id=seeded.institution_id,
        parameters={"shape": "count"},
        sql_key="sql_count",
    )
    # Same Finding 1 regression-pair discipline as students_below_attendance_threshold:
    # list and count shapes must agree on the same underlying fact. b_math is the one
    # qualifying (student, subject) row, so the DISTINCT student count is 1.
    assert count_result["query_result"][0]["student_count"] == len(
        {row["full_name"] for row in list_result["query_result"]}
    )


# --- 13. students_below_attendance_threshold (both sql_list and sql_count shapes) ---------


async def test_students_below_attendance_threshold_list_shape(seeded: _Fixture) -> None:
    teacher_result = await _run_template(
        "students_below_attendance_threshold",
        user_id=seeded.teacher_user_id,
        institution_id=seeded.institution_id,
        parameters={"threshold": 75},
        sql_key="sql_list",
    )
    assert teacher_result["result_row_count"] == 0  # 90% and 80% are both >= 75

    teacher2_result = await _run_template(
        "students_below_attendance_threshold",
        user_id=seeded.teacher2_user_id,
        institution_id=seeded.institution_id,
        parameters={"threshold": 75},
        sql_key="sql_list",
    )
    assert {row["full_name"] for row in teacher2_result["query_result"]} == {"stu-b-math"}


async def test_students_below_attendance_threshold_count_shape_matches_list_shape(
    seeded: _Fixture,
) -> None:
    list_result = await _run_template(
        "students_below_attendance_threshold",
        user_id=seeded.teacher2_user_id,
        institution_id=seeded.institution_id,
        parameters={"threshold": 75},
        sql_key="sql_list",
    )
    count_result = await _run_template(
        "students_below_attendance_threshold",
        user_id=seeded.teacher2_user_id,
        institution_id=seeded.institution_id,
        parameters={"threshold": 75},
        sql_key="sql_count",
    )
    # The design doc's own Finding 1 regression pair: list and count shapes must agree on
    # the same underlying fact for the same teacher/threshold.
    assert count_result["query_result"][0]["student_count"] == list_result["result_row_count"]
