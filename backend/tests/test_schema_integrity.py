"""Postgres schema integrity tests for the evaluation POC data model."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from education_platform.db.url import to_sync_url
from education_platform.modules.academics.models import (
    AcademicPeriod,
    AcademicPeriodStatus,
    EnrollmentStatus,
    Grade,
    GradeSubjectOffering,
    LearningOutcome,
    PeriodGrade,
    StudentGradeEnrollment,
    StudentSubjectEnrollment,
    Subject,
    Subtopic,
    Topic,
)
from education_platform.modules.assessments.models import (
    AttemptAnswer,
    CommonMasteryQuiz,
    Question,
    QuestionAnswerKey,
    QuestionOption,
    QuestionType,
    QuestionVersion,
    QuizAttempt,
    QuizAttemptStatus,
    QuizVersion,
)
from education_platform.modules.auth.models import (
    Institution,
    RoleName,
    StudentProfile,
    User,
    UserRole,
)
from education_platform.modules.generation.models import ContentGenerationRun
from education_platform.modules.generation.types import RunPhase
from education_platform.modules.materials.models import (
    SourceMaterial,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
)


def _institution(session: Session) -> Institution:
    institution = Institution(name=f"School {uuid4().hex[:8]}")
    session.add(institution)
    session.flush()
    return institution


def _lesson_material(session: Session) -> tuple[SourceMaterial, User]:
    institution = _institution(session)
    period = AcademicPeriod(
        institution_id=institution.id,
        name="2026-27",
        start_date=date(2026, 6, 1),
        end_date=date(2027, 3, 31),
        status=AcademicPeriodStatus.ACTIVE,
    )
    grade = Grade(institution_id=institution.id, name="Grade 8", sort_order=8)
    subject = Subject(institution_id=institution.id, name="Mathematics", code="MATH")
    user = User(
        institution_id=institution.id,
        email=f"teacher-{uuid4().hex[:8]}@example.com",
        full_name="Teacher",
        password_hash="x",
    )
    session.add_all([period, grade, subject, user])
    session.flush()
    period_grade = PeriodGrade(academic_period_id=period.id, grade_id=grade.id)
    session.add(period_grade)
    session.flush()
    offering = GradeSubjectOffering(period_grade_id=period_grade.id, subject_id=subject.id)
    session.add(offering)
    session.flush()
    topic = Topic(
        grade_subject_offering_id=offering.id, name="Geometry", slug="geometry", sequence=1
    )
    session.add(topic)
    session.flush()
    subtopic = Subtopic(
        topic_id=topic.id, name="Quadrilaterals", slug="rectangles_squares_properties", sequence=1
    )
    session.add(subtopic)
    session.flush()
    material = SourceMaterial(subtopic_id=subtopic.id, title="Lesson", slug="lesson")
    session.add(material)
    session.flush()
    return material, user


def test_migration_creates_core_tables(clean_db: str) -> None:
    engine = create_engine(to_sync_url(clean_db))
    names = set(inspect(engine).get_table_names())
    engine.dispose()
    for required in {
        "institutions",
        "users",
        "user_roles",
        "student_profiles",
        "academic_periods",
        "student_grade_enrollments",
        "student_subject_enrollments",
        "source_materials",
        "source_material_versions",
        "source_chunks",
        "knowledge_documents",
        "knowledge_document_versions",
        "knowledge_chunks",
        "ingest_jobs",
        "chunk_embeddings",
        "student_material_progress",
        "question_answer_keys",
        "quiz_attempts",
        "attempt_questions",
        "attempt_answers",
        "quiz_releases",
        "curriculum_generation_jobs",
    }:
        assert required in names


def test_email_unique_per_institution(db_session: Session) -> None:
    a = _institution(db_session)
    b = _institution(db_session)
    db_session.add_all(
        [
            User(
                institution_id=a.id,
                email="same@example.com",
                full_name="A",
                password_hash="x",
            ),
            User(
                institution_id=b.id,
                email="same@example.com",
                full_name="B",
                password_hash="x",
            ),
        ]
    )
    db_session.commit()

    db_session.add(
        User(
            institution_id=a.id,
            email="same@example.com",
            full_name="Dup",
            password_hash="x",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_one_active_academic_period_per_institution(db_session: Session) -> None:
    institution = _institution(db_session)
    db_session.add(
        AcademicPeriod(
            institution_id=institution.id,
            name="2025-26",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 3, 31),
            status=AcademicPeriodStatus.ACTIVE,
        )
    )
    db_session.commit()

    db_session.add(
        AcademicPeriod(
            institution_id=institution.id,
            name="2026-27",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 3, 31),
            status=AcademicPeriodStatus.ACTIVE,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_active_grade_enrollment_unique(db_session: Session) -> None:
    institution = _institution(db_session)
    period = AcademicPeriod(
        institution_id=institution.id,
        name="2026-27",
        start_date=date(2026, 6, 1),
        end_date=date(2027, 3, 31),
        status=AcademicPeriodStatus.ACTIVE,
    )
    grade = Grade(institution_id=institution.id, name="Grade 8", sort_order=8)
    student = StudentProfile(
        institution_id=institution.id,
        student_identifier="S1",
        full_name="Student One",
    )
    db_session.add_all([period, grade, student])
    db_session.flush()
    period_grade = PeriodGrade(academic_period_id=period.id, grade_id=grade.id)
    db_session.add(period_grade)
    db_session.flush()

    db_session.add(
        StudentGradeEnrollment(
            student_id=student.id,
            academic_period_id=period.id,
            period_grade_id=period_grade.id,
            status=EnrollmentStatus.ACTIVE,
        )
    )
    db_session.commit()

    db_session.add(
        StudentGradeEnrollment(
            student_id=student.id,
            academic_period_id=period.id,
            period_grade_id=period_grade.id,
            status=EnrollmentStatus.ACTIVE,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_one_published_material_version(db_session: Session) -> None:
    institution = _institution(db_session)
    period = AcademicPeriod(
        institution_id=institution.id,
        name="2026-27",
        start_date=date(2026, 6, 1),
        end_date=date(2027, 3, 31),
        status=AcademicPeriodStatus.ACTIVE,
    )
    grade = Grade(institution_id=institution.id, name="Grade 8", sort_order=8)
    subject = Subject(institution_id=institution.id, name="Mathematics", code="MATH")
    db_session.add_all([period, grade, subject])
    db_session.flush()
    period_grade = PeriodGrade(academic_period_id=period.id, grade_id=grade.id)
    db_session.add(period_grade)
    db_session.flush()
    offering = GradeSubjectOffering(period_grade_id=period_grade.id, subject_id=subject.id)
    db_session.add(offering)
    db_session.flush()
    topic = Topic(
        grade_subject_offering_id=offering.id, name="Geometry", slug="geometry", sequence=1
    )
    db_session.add(topic)
    db_session.flush()
    subtopic = Subtopic(
        topic_id=topic.id, name="Quadrilaterals", slug="rectangles_squares_properties", sequence=1
    )
    db_session.add(subtopic)
    db_session.flush()
    material = SourceMaterial(subtopic_id=subtopic.id, title="Lesson", slug="lesson")
    db_session.add(material)
    db_session.flush()

    db_session.add(
        SourceMaterialVersion(
            source_material_id=material.id,
            version_number=1,
            lifecycle_status=SourceMaterialVersionStatus.PUBLISHED,
            title="v1",
            content_markdown="# Hello",
            published_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    db_session.add(
        SourceMaterialVersion(
            source_material_id=material.id,
            version_number=2,
            lifecycle_status=SourceMaterialVersionStatus.PUBLISHED,
            title="v2",
            content_markdown="# Hello 2",
            published_at=datetime.now(UTC),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_one_in_flight_generation_run(db_session: Session) -> None:
    material, user = _lesson_material(db_session)
    subtopic = db_session.get(Subtopic, material.subtopic_id)
    assert subtopic is not None

    db_session.add(
        ContentGenerationRun(
            topic_id=subtopic.topic_id,
            submitted_by_user_id=user.id,
            title="one",
            phase=RunPhase.INDEXING,
            target_item_count=80,
        )
    )
    db_session.commit()
    db_session.add(
        ContentGenerationRun(
            topic_id=subtopic.topic_id,
            submitted_by_user_id=user.id,
            title="two",
            phase=RunPhase.OUTLINING,
            target_item_count=80,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_source_material_requires_exactly_one_parent(db_session: Session) -> None:
    material, _user = _lesson_material(db_session)
    subtopic = db_session.get(Subtopic, material.subtopic_id)
    assert subtopic is not None
    topic_id = subtopic.topic_id
    subtopic_id = subtopic.id
    db_session.commit()

    db_session.add(
        SourceMaterial(
            topic_id=topic_id,
            subtopic_id=subtopic_id,
            title="both",
            slug="both",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    db_session.add(SourceMaterial(title="neither", slug="neither"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    db_session.add(
        SourceMaterial(
            topic_id=topic_id,
            title="topic source",
            slug="source",
        )
    )
    db_session.commit()


def test_awaiting_approval_requires_markdown(db_session: Session) -> None:
    material, user = _lesson_material(db_session)
    db_session.add(
        SourceMaterialVersion(
            source_material_id=material.id,
            version_number=1,
            lifecycle_status=SourceMaterialVersionStatus.AWAITING_APPROVAL,
            title="empty",
            submitted_by_user_id=user.id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    material, user = _lesson_material(db_session)
    db_session.add(
        SourceMaterialVersion(
            source_material_id=material.id,
            version_number=1,
            lifecycle_status=SourceMaterialVersionStatus.AWAITING_APPROVAL,
            title="ready",
            content_markdown="# Lesson\n\n## Slide 1 — Start\n\nBody.\n",
            submitted_by_user_id=user.id,
        )
    )
    db_session.commit()


def test_answer_key_isolated_and_attempt_history(db_session: Session) -> None:
    institution = _institution(db_session)
    period = AcademicPeriod(
        institution_id=institution.id,
        name="2026-27",
        start_date=date(2026, 6, 1),
        end_date=date(2027, 3, 31),
        status=AcademicPeriodStatus.ACTIVE,
    )
    grade = Grade(institution_id=institution.id, name="Grade 8", sort_order=8)
    subject = Subject(institution_id=institution.id, name="Mathematics", code="MATH")
    student = StudentProfile(
        institution_id=institution.id, student_identifier="S2", full_name="Student Two"
    )
    db_session.add_all([period, grade, subject, student])
    db_session.flush()
    period_grade = PeriodGrade(academic_period_id=period.id, grade_id=grade.id)
    db_session.add(period_grade)
    db_session.flush()
    offering = GradeSubjectOffering(period_grade_id=period_grade.id, subject_id=subject.id)
    db_session.add(offering)
    db_session.flush()
    topic = Topic(grade_subject_offering_id=offering.id, name="Number", slug="number", sequence=1)
    db_session.add(topic)
    db_session.flush()
    subtopic = Subtopic(topic_id=topic.id, name="Roots", slug="roots", sequence=1)
    db_session.add(subtopic)
    db_session.flush()
    outcome = LearningOutcome(
        subtopic_id=subtopic.id, code="LO1", statement="Know squares", sequence=1
    )
    question = Question(subtopic_id=subtopic.id, code="Q1")
    quiz = CommonMasteryQuiz(subtopic_id=subtopic.id, title="Roots quiz")
    db_session.add_all([outcome, question, quiz])
    db_session.flush()

    qv = QuestionVersion(
        question_id=question.id,
        version_number=1,
        prompt="What is 8 squared?",
        question_type=QuestionType.MULTIPLE_CHOICE,
        marks=Decimal("1.00"),
    )
    db_session.add(qv)
    db_session.flush()
    db_session.add_all(
        [
            QuestionOption(question_version_id=qv.id, label="A", text="16", sequence=1),
            QuestionOption(question_version_id=qv.id, label="B", text="64", sequence=2),
            QuestionAnswerKey(question_version_id=qv.id, correct_option_label="B"),
        ]
    )
    quiz_version = QuizVersion(quiz_id=quiz.id, version_number=1)
    db_session.add(quiz_version)
    db_session.flush()

    # Student-facing style query: options without joining answer keys.
    options = db_session.scalars(
        select(QuestionOption).where(QuestionOption.question_version_id == qv.id)
    ).all()
    assert {o.label for o in options} == {"A", "B"}
    keys = db_session.scalars(
        select(QuestionAnswerKey).where(QuestionAnswerKey.question_version_id == qv.id)
    ).all()
    assert len(keys) == 1
    assert keys[0].correct_option_label == "B"

    attempt = QuizAttempt(
        student_id=student.id,
        quiz_version_id=quiz_version.id,
        attempt_number=1,
        status=QuizAttemptStatus.SCORED,
        score_raw=Decimal("1.00"),
        score_percent=Decimal("100.00"),
        passed=True,
    )
    db_session.add(attempt)
    db_session.flush()
    db_session.add(
        AttemptAnswer(
            attempt_id=attempt.id,
            question_version_id=qv.id,
            selected_option_label="B",
            is_correct=True,
            marks_awarded=Decimal("1.00"),
        )
    )
    db_session.commit()

    loaded = db_session.get(QuizAttempt, attempt.id)
    assert loaded is not None
    assert loaded.quiz_version_id == quiz_version.id
    answers = db_session.scalars(
        select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id)
    ).all()
    assert len(answers) == 1
    assert answers[0].question_version_id == qv.id


def test_user_role_unique(db_session: Session) -> None:
    institution = _institution(db_session)
    user = User(
        institution_id=institution.id,
        email="teacher@example.com",
        full_name="Teacher",
        password_hash="x",
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserRole(user_id=user.id, role=RoleName.TEACHER))
    db_session.commit()
    db_session.add(UserRole(user_id=user.id, role=RoleName.TEACHER))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_subject_enrollment_unique_when_active(db_session: Session) -> None:
    institution = _institution(db_session)
    period = AcademicPeriod(
        institution_id=institution.id,
        name="2026-27",
        start_date=date(2026, 6, 1),
        end_date=date(2027, 3, 31),
        status=AcademicPeriodStatus.ACTIVE,
    )
    grade = Grade(institution_id=institution.id, name="Grade 8", sort_order=8)
    subject = Subject(institution_id=institution.id, name="Mathematics", code="MATH")
    student = StudentProfile(
        institution_id=institution.id, student_identifier="S3", full_name="Student Three"
    )
    db_session.add_all([period, grade, subject, student])
    db_session.flush()
    period_grade = PeriodGrade(academic_period_id=period.id, grade_id=grade.id)
    db_session.add(period_grade)
    db_session.flush()
    offering = GradeSubjectOffering(period_grade_id=period_grade.id, subject_id=subject.id)
    grade_enrollment = StudentGradeEnrollment(
        student_id=student.id,
        academic_period_id=period.id,
        period_grade_id=period_grade.id,
        status=EnrollmentStatus.ACTIVE,
    )
    db_session.add_all([offering, grade_enrollment])
    db_session.flush()
    db_session.add(
        StudentSubjectEnrollment(
            student_id=student.id,
            grade_enrollment_id=grade_enrollment.id,
            grade_subject_offering_id=offering.id,
            status=EnrollmentStatus.ACTIVE,
        )
    )
    db_session.commit()
    db_session.add(
        StudentSubjectEnrollment(
            student_id=student.id,
            grade_enrollment_id=grade_enrollment.id,
            grade_subject_offering_id=offering.id,
            status=EnrollmentStatus.ACTIVE,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
