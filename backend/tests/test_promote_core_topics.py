"""Synthetic Core topics become real topics without dropping attempts."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, func, select
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
    Subtopic,
    Topic,
)
from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    Question,
    QuestionType,
    QuestionVersion,
    QuestionVersionStatus,
    QuizAttempt,
    QuizAttemptStatus,
    QuizItem,
    QuizScope,
    QuizVersion,
    QuizVersionStatus,
)
from education_platform.modules.auth.models import (
    Institution,
    InstitutionStatus,
    StudentProfile,
    StudentProfileStatus,
    User,
    UserStatus,
)
from education_platform.modules.generation.models import ContentGenerationRun
from education_platform.modules.generation.types import RunPhase
from education_platform.modules.materials.models import (
    MaterialProgressStatus,
    SourceMaterial,
    SourceMaterialStatus,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
    StudentMaterialProgress,
)
from education_platform.modules.synthetic.promote import promote_core_topics


def _old_core(session: Session, *, with_generation_run: bool = False) -> dict[str, object]:
    institution = Institution(name="Core Migration School", status=InstitutionStatus.ACTIVE)
    session.add(institution)
    session.flush()
    user = User(
        institution_id=institution.id,
        email="teacher@core.school",
        full_name="Core Teacher",
        password_hash="x",
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    session.flush()
    grade = Grade(institution_id=institution.id, name="Grade 8", sort_order=8)
    subject = Subject(institution_id=institution.id, name="Mathematics", code="MATH")
    period = AcademicPeriod(
        institution_id=institution.id,
        name="2026",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 1),
        status=AcademicPeriodStatus.ACTIVE,
    )
    session.add_all([grade, subject, period])
    session.flush()
    period_grade = PeriodGrade(academic_period_id=period.id, grade_id=grade.id)
    session.add(period_grade)
    session.flush()
    section = Section(period_grade_id=period_grade.id, name="8A")
    offering = GradeSubjectOffering(period_grade_id=period_grade.id, subject_id=subject.id)
    session.add_all([section, offering])
    session.flush()
    core = Topic(
        grade_subject_offering_id=offering.id,
        name="Mathematics Core",
        slug="math-core",
        sequence=1,
    )
    session.add(core)
    session.flush()
    fractions = Subtopic(topic_id=core.id, name="Fractions", slug="fractions", sequence=1)
    session.add(fractions)
    session.flush()

    def _lesson(
        parent_topic: Topic | None, parent_subtopic: Subtopic | None, title: str
    ) -> SourceMaterialVersion:
        material = SourceMaterial(
            topic_id=parent_topic.id if parent_topic else None,
            subtopic_id=parent_subtopic.id if parent_subtopic else None,
            title=title,
            slug="lesson",
            status=SourceMaterialStatus.PUBLISHED,
        )
        session.add(material)
        session.flush()
        version = SourceMaterialVersion(
            source_material_id=material.id,
            version_number=1,
            lifecycle_status=SourceMaterialVersionStatus.PUBLISHED,
            title=title,
            content_markdown=f"# {title}\n",
            content_format="markdown",
            submitted_by_user_id=user.id,
            published_at=datetime.now(UTC),
        )
        session.add(version)
        session.flush()
        return version

    child_lesson = _lesson(None, fractions, "Fractions")
    overview = _lesson(core, None, "Mathematics Core")
    notes = SourceMaterial(
        subtopic_id=fractions.id,
        title="Teacher notes",
        slug="notes",
        status=SourceMaterialStatus.PUBLISHED,
    )
    session.add(notes)
    session.flush()
    notes_version = SourceMaterialVersion(
        source_material_id=notes.id,
        version_number=1,
        lifecycle_status=SourceMaterialVersionStatus.PUBLISHED,
        title="Teacher notes",
        content_format="pdf",
        blob_object_key="uploads/fractions.pdf",
        submitted_by_user_id=user.id,
    )
    session.add(notes_version)
    question = Question(subtopic_id=fractions.id, code="fractions-1")
    session.add(question)
    session.flush()
    question_version = QuestionVersion(
        question_id=question.id,
        version_number=1,
        prompt="Which fraction is equivalent?",
        question_type=QuestionType.MULTIPLE_CHOICE,
        marks=Decimal("1.00"),
        lifecycle_status=QuestionVersionStatus.PUBLISHED,
    )
    session.add(question_version)
    session.flush()
    quiz = CommonMasteryQuiz(
        quiz_scope=QuizScope.SUBTOPIC_MASTERY,
        subtopic_id=fractions.id,
        title="Fractions mastery",
    )
    session.add(quiz)
    session.flush()
    quiz_version = QuizVersion(
        quiz_id=quiz.id,
        version_number=1,
        lifecycle_status=QuizVersionStatus.RELEASED,
        max_attempts=3,
        pass_threshold_percent=Decimal("70.00"),
    )
    session.add(quiz_version)
    session.flush()
    session.add(
        QuizItem(
            quiz_version_id=quiz_version.id,
            question_version_id=question_version.id,
            sequence=1,
        )
    )
    student_user = User(
        institution_id=institution.id,
        email="student@core.school",
        full_name="Core Student",
        password_hash="x",
        status=UserStatus.ACTIVE,
    )
    session.add(student_user)
    session.flush()
    profile = StudentProfile(
        institution_id=institution.id,
        user_id=student_user.id,
        student_identifier="C-1",
        full_name="Core Student",
        status=StudentProfileStatus.ACTIVE,
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
    now = datetime.now(UTC)
    session.add_all(
        [
            StudentMaterialProgress(
                student_subject_enrollment_id=subject_enrollment.id,
                source_material_version_id=child_lesson.id,
                status=MaterialProgressStatus.COMPLETED,
                opened_at=now,
                last_opened_at=now,
                completed_at=now,
            ),
            StudentMaterialProgress(
                student_subject_enrollment_id=subject_enrollment.id,
                source_material_version_id=overview.id,
                status=MaterialProgressStatus.COMPLETED,
                opened_at=now,
                last_opened_at=now,
                completed_at=now,
            ),
        ]
    )
    attempt = QuizAttempt(
        student_id=profile.id,
        student_subject_enrollment_id=subject_enrollment.id,
        quiz_version_id=quiz_version.id,
        attempt_number=1,
        status=QuizAttemptStatus.RELEASED,
        score_percent=Decimal("80.00"),
        passed=True,
    )
    session.add(attempt)
    session.flush()
    if with_generation_run:
        session.add(
            ContentGenerationRun(
                topic_id=core.id,
                submitted_by_user_id=user.id,
                title="Mathematics Core",
                phase=RunPhase.PUBLISHED,
                target_item_count=60,
                published_lesson_version_id=overview.id,
            )
        )
        session.flush()
    return {
        "core_id": core.id,
        "lesson_version_id": child_lesson.id,
        "overview_version_id": overview.id,
        "notes_id": notes.id,
        "quiz_id": quiz.id,
        "quiz_version_id": quiz_version.id,
        "question_id": question.id,
        "attempt_id": attempt.id,
        "offering_id": offering.id,
    }


def test_promote_core_twice_keeps_attempts(clean_db: str) -> None:
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        seeded = _old_core(session)
        session.commit()
        attempt_count = session.scalar(select(func.count()).select_from(QuizAttempt))
        first = promote_core_topics(session)
        session.commit()
        assert first.deleted_core_topics == 1
        assert first.promoted_topics == 1
        assert first.skipped == []
        assert session.get(Topic, seeded["core_id"]) is None
        assert session.get(SourceMaterialVersion, seeded["overview_version_id"]) is None
        overview_progress = session.scalar(
            select(func.count())
            .select_from(StudentMaterialProgress)
            .where(
                StudentMaterialProgress.source_material_version_id == seeded["overview_version_id"]
            )
        )
        assert overview_progress == 0
        lesson = session.get(SourceMaterialVersion, seeded["lesson_version_id"])
        assert lesson is not None
        lesson_material = session.get(SourceMaterial, lesson.source_material_id)
        assert lesson_material is not None and lesson_material.topic_id is not None
        topic = session.get(Topic, lesson_material.topic_id)
        assert topic is not None and topic.name == "Fractions"
        assert topic.slug == "fractions"
        child_progress = session.scalar(
            select(StudentMaterialProgress).where(
                StudentMaterialProgress.source_material_version_id == lesson.id,
                StudentMaterialProgress.status == MaterialProgressStatus.COMPLETED,
            )
        )
        assert child_progress is not None
        quiz = session.get(CommonMasteryQuiz, seeded["quiz_id"])
        assert quiz is not None
        assert quiz.quiz_scope == QuizScope.TOPIC_MASTERY
        assert quiz.topic_id == topic.id
        assert quiz.subtopic_id is None
        assert quiz.title == "Fractions topic mastery"
        quiz_version = session.get(QuizVersion, seeded["quiz_version_id"])
        assert quiz_version is not None and quiz_version.max_attempts is None
        attempt = session.get(QuizAttempt, seeded["attempt_id"])
        assert attempt is not None
        assert attempt.score_percent == Decimal("80.00")
        assert attempt.quiz_version_id == quiz_version.id
        assert session.scalar(select(func.count()).select_from(QuizAttempt)) == attempt_count
        question = session.get(Question, seeded["question_id"])
        assert question is not None
        tagged = session.get(Subtopic, question.subtopic_id)
        assert tagged is not None and tagged.topic_id == topic.id
        assert tagged.name == "Equivalent fractions"
        notes = session.get(SourceMaterial, seeded["notes_id"])
        assert notes is not None and notes.topic_id == topic.id
        assert notes.slug == "notes"
        core_named = session.scalar(select(Topic).where(Topic.name == "Mathematics Core"))
        assert core_named is None

        second = promote_core_topics(session)
        session.commit()
        assert second.promoted_topics == 0
        assert second.deleted_core_topics == 0
        assert second.skipped == []
        assert session.scalar(select(func.count()).select_from(QuizAttempt)) == attempt_count
        assert session.get(QuizAttempt, seeded["attempt_id"]) is not None
        fractions_count = session.scalar(
            select(func.count())
            .select_from(Topic)
            .where(
                Topic.grade_subject_offering_id == seeded["offering_id"],
                Topic.name == "Fractions",
            )
        )
        assert fractions_count == 1
    engine.dispose()


def test_promote_skips_core_with_a_generation_run(clean_db: str) -> None:
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        seeded = _old_core(session, with_generation_run=True)
        session.commit()
        result = promote_core_topics(session)
        session.commit()
        assert result.promoted_topics == 1
        assert result.deleted_core_topics == 0
        assert result.skipped
        assert session.get(Topic, seeded["core_id"]) is not None
        assert session.get(SourceMaterialVersion, seeded["overview_version_id"]) is not None
        assert session.get(QuizAttempt, seeded["attempt_id"]) is not None
        again = promote_core_topics(session)
        session.commit()
        assert again.deleted_core_topics == 0
        assert session.get(QuizAttempt, seeded["attempt_id"]) is not None
    engine.dispose()
