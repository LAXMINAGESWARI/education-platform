"""Synthetic lessons follow the published-material and open-quiz rules."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from education_platform.db.url import to_sync_url
from education_platform.modules.academics.models import (
    AcademicPeriod,
    Grade,
    GradeSubjectOffering,
    PeriodGrade,
    Subject,
    Subtopic,
    Topic,
)
from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    QuizAttempt,
    QuizRelease,
    QuizReleaseStatus,
    QuizScope,
    QuizVersion,
)
from education_platform.modules.assistant.models import ChatConversation
from education_platform.modules.at_risk.models import AtRiskFlag, AtRiskStatus, AtRiskTier
from education_platform.modules.auth.models import (
    Institution,
    InstitutionStatus,
    StudentProfile,
    User,
)
from education_platform.modules.materials.markdown_parser import parse_slides
from education_platform.modules.materials.models import (
    MaterialProgressStatus,
    SourceMaterial,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
    StudentMaterialProgress,
)
from education_platform.modules.rag.models import KnowledgeDocument, KnowledgeDocumentStatus
from education_platform.modules.synthetic.generator import SchoolSpec, generate_school
from education_platform.modules.synthetic.lessons import (
    subtopic_lesson_markdown,
    topic_lesson_markdown,
)

SPEC = SchoolSpec(sections_per_grade=2, students_per_section=3, term_weeks=2)


def _login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "demo1234",
            "institution_name": SPEC.institution_name,
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_lesson_markdown_depends_only_on_the_seed() -> None:
    kwargs = {
        "grade_name": "Grade 6",
        "subject_name": "Mathematics",
        "title": "Fractions",
    }
    assert subtopic_lesson_markdown(seed=20260812, **kwargs) == subtopic_lesson_markdown(
        seed=20260812, **kwargs
    )
    assert subtopic_lesson_markdown(seed=20260812, **kwargs) != subtopic_lesson_markdown(
        seed=20260813, **kwargs
    )
    overview = topic_lesson_markdown(
        seed=20260812,
        grade_name="Grade 6",
        subject_name="Mathematics",
        title="Mathematics Core",
        units=("Fractions", "Decimals"),
    )
    assert parse_slides(overview)
    assert "Fractions" in overview


def test_published_lessons_unlock_attempted_quizzes(client: TestClient, clean_db: str) -> None:
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    expected_lessons = len(SPEC.grades) * len(SPEC.subjects) * (1 + SPEC.quizzes_per_subject)
    with Session(engine) as session:
        result = generate_school(session, SPEC)
        session.commit()
        assert result.lessons == expected_lessons
        published = session.scalars(
            select(SourceMaterialVersion)
            .join(User, User.id == SourceMaterialVersion.submitted_by_user_id)
            .join(Institution, Institution.id == User.institution_id)
            .where(
                Institution.name == SPEC.institution_name,
                SourceMaterialVersion.lifecycle_status == SourceMaterialVersionStatus.PUBLISHED,
            )
        ).all()
        assert len(published) == expected_lessons
        assert all(version.content_markdown for version in published)
        assert all(version.published_at is not None for version in published)
        assert all(version.submitted_by_user_id is not None for version in published)

        passed = session.scalar(select(QuizAttempt).where(QuizAttempt.passed.is_(True)).limit(1))
        failed = session.scalar(select(QuizAttempt).where(QuizAttempt.passed.is_(False)).limit(1))
        assert passed is not None and failed is not None
        for attempt in (passed, failed):
            quiz_version = session.get(QuizVersion, attempt.quiz_version_id)
            assert quiz_version is not None
            quiz = session.get(CommonMasteryQuiz, quiz_version.quiz_id)
            assert quiz is not None and quiz.subtopic_id is not None
            material = session.scalar(
                select(SourceMaterial).where(
                    SourceMaterial.subtopic_id == quiz.subtopic_id,
                    SourceMaterial.slug == "lesson",
                )
            )
            assert material is not None
            version = session.scalar(
                select(SourceMaterialVersion).where(
                    SourceMaterialVersion.source_material_id == material.id,
                    SourceMaterialVersion.lifecycle_status == SourceMaterialVersionStatus.PUBLISHED,
                )
            )
            assert version is not None
            progress = session.scalar(
                select(StudentMaterialProgress).where(
                    StudentMaterialProgress.student_subject_enrollment_id
                    == attempt.student_subject_enrollment_id,
                    StudentMaterialProgress.source_material_version_id == version.id,
                    StudentMaterialProgress.status == MaterialProgressStatus.COMPLETED,
                )
            )
            assert progress is not None

        release = session.scalar(
            select(QuizRelease)
            .join(User, User.id == QuizRelease.released_by_user_id)
            .join(Institution, Institution.id == User.institution_id)
            .where(
                Institution.name == SPEC.institution_name,
                QuizRelease.status == QuizReleaseStatus.OPEN,
            )
            .limit(1)
        )
        assert release is not None
        now = datetime.now(UTC)
        assert release.window_starts_at is not None and release.window_starts_at <= now
        assert release.window_ends_at is not None and release.window_ends_at >= now
        fractions = session.scalar(
            select(Subtopic)
            .join(Topic, Topic.id == Subtopic.topic_id)
            .join(GradeSubjectOffering, GradeSubjectOffering.id == Topic.grade_subject_offering_id)
            .join(Subject, Subject.id == GradeSubjectOffering.subject_id)
            .join(PeriodGrade, PeriodGrade.id == GradeSubjectOffering.period_grade_id)
            .join(Grade, Grade.id == PeriodGrade.grade_id)
            .join(AcademicPeriod, AcademicPeriod.id == PeriodGrade.academic_period_id)
            .join(Institution, Institution.id == AcademicPeriod.institution_id)
            .where(
                Institution.name == SPEC.institution_name,
                Subject.code == "MATH",
                Grade.name == "Grade 6",
                Subtopic.name == "Fractions",
            )
        )
        assert fractions is not None
        fractions_quiz = session.scalar(
            select(CommonMasteryQuiz).where(
                CommonMasteryQuiz.subtopic_id == fractions.id,
                CommonMasteryQuiz.quiz_scope == QuizScope.SUBTOPIC_MASTERY,
            )
        )
        assert fractions_quiz is not None
        topic_id = fractions.topic_id
        fractions_id = fractions.id
        quiz_id = fractions_quiz.id
    engine.dispose()

    headers = _login(client, "student1@alnoor.school")
    directory = client.get("/api/v1/me/learning-directory", headers=headers)
    assert directory.status_code == 200, directory.text
    math = next(row for row in directory.json()["subjects"] if row["name"] == "Mathematics")
    assert math["grade_name"] == "Grade 6"
    unit = next(
        subtopic
        for topic in math["topics"]
        for subtopic in topic["subtopics"]
        if subtopic["title"] == "Fractions"
    )
    assert unit["has_lesson"] is True
    assert unit["lesson_completed"] is True
    assert unit["quiz"]["unlocked"] is True

    lesson = client.get(f"/api/v1/subtopics/{fractions_id}/material", headers=headers)
    assert lesson.status_code == 200, lesson.text
    assert lesson.json()["slides"]
    topic_lesson = client.get(f"/api/v1/topics/{topic_id}/material", headers=headers)
    assert topic_lesson.status_code == 200, topic_lesson.text
    assert topic_lesson.json()["slides"]

    started = client.post(f"/api/v1/quizzes/{quiz_id}/attempts", headers=headers)
    assert 200 <= started.status_code < 300, started.text

    grade7 = _login(client, "student7@alnoor.school")
    other = client.get("/api/v1/me/learning-directory", headers=grade7)
    assert other.status_code == 200, other.text
    assert {row["grade_name"] for row in other.json()["subjects"]} == {"Grade 7"}
    science = next(row for row in other.json()["subjects"] if row["name"] == "Science")
    subtopic_id = science["topics"][0]["subtopics"][0]["id"]
    other_lesson = client.get(f"/api/v1/subtopics/{subtopic_id}/material", headers=grade7)
    assert other_lesson.status_code == 200, other_lesson.text
    other_quiz = science["topics"][0]["subtopics"][0]["quiz"]["id"]
    other_attempt = client.post(f"/api/v1/quizzes/{other_quiz}/attempts", headers=grade7)
    assert 200 <= other_attempt.status_code < 300, other_attempt.text


def test_rebuild_keeps_uploads_and_other_schools(clean_db: str) -> None:
    """Reseed replaces Al Noor accounts. Knowledge documents and other schools stay."""
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        first = generate_school(session, SPEC)
        session.commit()
        user = session.scalar(select(User).where(User.email == "student1@alnoor.school"))
        assert user is not None
        student = session.scalar(select(StudentProfile).where(StudentProfile.user_id == user.id))
        period = session.scalar(
            select(AcademicPeriod).where(AcademicPeriod.institution_id == first.institution_id)
        )
        assert student is not None and period is not None
        session.add(
            ChatConversation(
                institution_id=first.institution_id,
                owner_user_id=user.id,
                title="QA chat",
            )
        )
        session.add(
            AtRiskFlag(
                institution_id=first.institution_id,
                student_id=student.id,
                academic_period_id=period.id,
                tier=AtRiskTier.ATTENTION,
                drivers=[{"metric": "attendance", "value": 62}],
                status=AtRiskStatus.ACTIVE,
                computed_at=datetime.now(UTC),
            )
        )
        document = KnowledgeDocument(
            institution_id=first.institution_id,
            title="QA- policy",
            slug="qa-policy",
            doc_type="policy",
            status=KnowledgeDocumentStatus.DRAFT,
            required_roles=["administrator"],
        )
        other = Institution(name="Other School", status=InstitutionStatus.ACTIVE)
        session.add(document)
        session.add(other)
        session.commit()
        document_id = document.id
        other_id = other.id

        second = generate_school(session, SPEC)
        session.commit()
        assert second.students == first.students == 30
        assert session.get(KnowledgeDocument, document_id) is not None
        assert session.get(Institution, other_id) is not None
        assert session.scalar(select(func.count()).select_from(ChatConversation)) == 0
        assert session.scalar(select(func.count()).select_from(AtRiskFlag)) == 0
        still_published = session.scalar(
            select(func.count())
            .select_from(SourceMaterialVersion)
            .where(
                SourceMaterialVersion.lifecycle_status == SourceMaterialVersionStatus.PUBLISHED,
                SourceMaterialVersion.submitted_by_user_id.is_not(None),
            )
        )
        assert still_published == len(SPEC.grades) * len(SPEC.subjects) * (
            1 + SPEC.quizzes_per_subject
        )
    engine.dispose()
