from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from education_platform.db.url import to_sync_url
from education_platform.modules.academics.models import (
    AcademicPeriod,
    Grade,
    GradeSubjectOffering,
    PeriodGrade,
    Subject,
    Subtopic,
    TeachingAssignment,
    TeachingAssignmentStatus,
    Topic,
)
from education_platform.modules.auth.models import Institution, RoleName, User, UserRole, UserStatus
from education_platform.modules.auth.security import hash_password
from education_platform.modules.materials.seed import (
    POC_GRADE_NAME,
    POC_INSTITUTION_NAME,
    POC_PERIOD_NAME,
    POC_SUBJECT_CODE,
)
from education_platform.modules.synthetic.generator import SchoolSpec, generate_school

POC_TEACHER_EMAIL = "math.teacher@demo.school"
POC_TEACHER_PASSWORD = "demo1234"
ALNOOR_SPEC = SchoolSpec(sections_per_grade=2, students_per_section=3, term_weeks=2)
ALNOOR_TEACHER = "meera.krishnan@alnoor.school"


def _alnoor_subtopic(session: Session, *, subject_code: str, grade_name: str) -> Subtopic:
    subtopic = session.scalar(
        select(Subtopic)
        .join(Topic, Topic.id == Subtopic.topic_id)
        .join(
            GradeSubjectOffering,
            GradeSubjectOffering.id == Topic.grade_subject_offering_id,
        )
        .join(Subject, Subject.id == GradeSubjectOffering.subject_id)
        .join(PeriodGrade, PeriodGrade.id == GradeSubjectOffering.period_grade_id)
        .join(Grade, Grade.id == PeriodGrade.grade_id)
        .join(AcademicPeriod, AcademicPeriod.id == PeriodGrade.academic_period_id)
        .join(Institution, Institution.id == AcademicPeriod.institution_id)
        .where(
            Institution.name == ALNOOR_SPEC.institution_name,
            Subject.code == subject_code,
            Grade.name == grade_name,
        )
        .order_by(Subtopic.sequence)
    )
    assert subtopic is not None
    return subtopic


def test_list_materials(client: TestClient, enrolled_student_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/materials", headers=enrolled_student_headers)
    assert response.status_code == 200
    topics = response.json()
    ids = {topic["id"] for topic in topics}
    assert "rectangles_squares_properties" in ids
    assert "square_numbers_patterns" in ids
    for topic in topics:
        assert topic["has_lesson"] is True
        assert topic["has_quiz"] is True
        assert topic["title"]


def test_get_lesson_includes_slides(
    client: TestClient, enrolled_student_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/materials/rectangles_squares_properties", headers=enrolled_student_headers
    )
    assert response.status_code == 200
    payload = response.json()
    UUID(payload["id"])
    assert "Rectangles" in payload["title"]
    assert "## Slide 1" in payload["markdown"]
    assert len(payload["slides"]) >= 1
    assert payload["slides"][0]["number"] == 1
    assert payload["slides"][0]["title"]
    assert payload["slides"][0]["content"]


def test_get_quiz_strips_answer_key(
    client: TestClient, enrolled_student_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/materials/square_numbers_patterns/quiz", headers=enrolled_student_headers
    )
    assert response.status_code == 200
    payload = response.json()
    UUID(payload["id"])
    assert len(payload["questions"]) == 10
    first = payload["questions"][0]
    assert first["number"] == 1
    assert first["difficulty"] == "Easy"
    assert len(first["options"]) == 4
    assert {option["label"] for option in first["options"]} == {"A", "B", "C", "D"}
    serialized = str(payload)
    assert "Answer Key" not in serialized
    assert "**B** —" not in serialized
    assert "1027 ends in 7" not in serialized
    assert "correct_option_label" not in serialized


def test_unknown_topic_returns_404(
    client: TestClient, enrolled_student_headers: dict[str, str]
) -> None:
    assert (
        client.get("/api/v1/materials/does_not_exist", headers=enrolled_student_headers).status_code
        == 404
    )
    assert (
        client.get(
            "/api/v1/materials/does_not_exist/quiz", headers=enrolled_student_headers
        ).status_code
        == 404
    )


def test_unenrolled_student_cannot_see_materials(client: TestClient) -> None:
    provision = client.post(
        "/api/v1/auth/provision-student",
        json={
            "email": "noenroll@example.com",
            "password": "password123",
            "full_name": "No Enroll",
            "student_identifier": "S-404",
        },
    )
    assert provision.status_code == 200
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "noenroll@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    listed = client.get("/api/v1/materials", headers=headers)
    assert listed.status_code == 200
    assert listed.json() == []
    assert (
        client.get("/api/v1/materials/rectangles_squares_properties", headers=headers).status_code
        == 404
    )


def _login(client: TestClient, email: str, password: str, institution_name: str) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password, "institution_name": institution_name},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _assign_poc_teacher(db: Session) -> None:
    institution = db.scalar(select(Institution).where(Institution.name == POC_INSTITUTION_NAME))
    assert institution is not None
    user = User(
        institution_id=institution.id,
        email=POC_TEACHER_EMAIL,
        full_name="POC Math Teacher",
        password_hash=hash_password(POC_TEACHER_PASSWORD),
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    db.flush()
    db.add(UserRole(user_id=user.id, role=RoleName.TEACHER))
    period = db.scalar(
        select(AcademicPeriod).where(
            AcademicPeriod.institution_id == institution.id,
            AcademicPeriod.name == POC_PERIOD_NAME,
        )
    )
    grade = db.scalar(
        select(Grade).where(Grade.institution_id == institution.id, Grade.name == POC_GRADE_NAME)
    )
    subject = db.scalar(
        select(Subject).where(
            Subject.institution_id == institution.id, Subject.code == POC_SUBJECT_CODE
        )
    )
    assert period is not None and grade is not None and subject is not None
    period_grade = db.scalar(
        select(PeriodGrade).where(
            PeriodGrade.academic_period_id == period.id, PeriodGrade.grade_id == grade.id
        )
    )
    assert period_grade is not None
    offering = db.scalar(
        select(GradeSubjectOffering).where(
            GradeSubjectOffering.period_grade_id == period_grade.id,
            GradeSubjectOffering.subject_id == subject.id,
        )
    )
    assert offering is not None
    db.add(
        TeachingAssignment(
            teacher_user_id=user.id,
            academic_period_id=period.id,
            grade_subject_offering_id=offering.id,
            section_id=None,
            status=TeachingAssignmentStatus.ACTIVE,
        )
    )
    db.commit()


def test_teacher_reads_taught_lesson_and_quiz(client: TestClient, seeded_db: Session) -> None:
    _assign_poc_teacher(seeded_db)
    headers = _login(client, POC_TEACHER_EMAIL, POC_TEACHER_PASSWORD, POC_INSTITUTION_NAME)

    lesson = client.get("/api/v1/materials/rectangles_squares_properties", headers=headers)
    assert lesson.status_code == 200, lesson.text
    assert lesson.json()["progress"] is None

    quiz = client.get("/api/v1/materials/square_numbers_patterns/quiz", headers=headers)
    assert quiz.status_code == 200, quiz.text
    payload = quiz.json()
    assert len(payload["questions"]) == 10
    serialized = str(payload)
    assert "Answer Key" not in serialized
    assert "correct_option_label" not in serialized

    lesson_id = lesson.json()["id"]
    progress = client.put(
        f"/api/v1/subtopics/{lesson_id}/material-progress",
        headers=headers,
        json={"status": "completed"},
    )
    assert progress.status_code == 403
    assert progress.json()["detail"] == "Student enrollment required"

    enrollments = client.get("/api/v1/me/enrollments", headers=headers)
    assert enrollments.status_code == 200
    assert enrollments.json()["grade_enrollments"] == []
    assert enrollments.json()["subject_enrollments"] == []


def test_teacher_directory_and_other_subject_404(client: TestClient, clean_db: str) -> None:
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        generate_school(session, ALNOOR_SPEC)
        session.commit()
        english_subtopic = _alnoor_subtopic(session, subject_code="ENG", grade_name="Grade 8")
        taught_subtopic = _alnoor_subtopic(session, subject_code="MATH", grade_name="Grade 8")
        english_id = english_subtopic.id
        taught_id = taught_subtopic.id
        taught_slug = taught_subtopic.slug
        taught_topic_id = taught_subtopic.topic_id
    engine.dispose()

    headers = _login(client, ALNOOR_TEACHER, "demo1234", ALNOOR_SPEC.institution_name)
    directory = client.get("/api/v1/me/learning-directory", headers=headers)
    assert directory.status_code == 200, directory.text
    names = {(row["grade_name"], row["name"]) for row in directory.json()["subjects"]}
    assert ("Grade 8", "Mathematics") in names
    assert ("Grade 9", "Science") in names
    assert ("Grade 8", "English") not in names

    other = client.get(f"/api/v1/subtopics/{english_id}/material", headers=headers)
    assert other.status_code == 404

    # Diagnostic subtopics tag questions. They are not quizzes.
    taught_quiz = client.get(f"/api/v1/materials/{taught_slug}/quiz", headers=headers)
    assert taught_quiz.status_code == 404, taught_quiz.text
    by_id = client.get(f"/api/v1/subtopics/{taught_id}/quiz", headers=headers)
    assert by_id.status_code == 404, by_id.text

    lesson = client.get(f"/api/v1/topics/{taught_topic_id}/material", headers=headers)
    assert lesson.status_code == 200, lesson.text
    assert "correct_option_label" not in str(lesson.json())
    math = next(
        row
        for row in directory.json()["subjects"]
        if row["grade_name"] == "Grade 8" and row["name"] == "Mathematics"
    )
    topic = next(item for item in math["topics"] if item["id"] == str(taught_topic_id))
    assert topic["has_topic_lesson"] is True
    assert topic["overall_quiz"]["id"] == lesson.json()["quiz_id"]


def _alnoor_topic(session: Session, *, subject_code: str, grade_name: str, slug: str) -> Topic:
    topic = session.scalar(
        select(Topic)
        .join(
            GradeSubjectOffering,
            GradeSubjectOffering.id == Topic.grade_subject_offering_id,
        )
        .join(Subject, Subject.id == GradeSubjectOffering.subject_id)
        .join(PeriodGrade, PeriodGrade.id == GradeSubjectOffering.period_grade_id)
        .join(Grade, Grade.id == PeriodGrade.grade_id)
        .join(AcademicPeriod, AcademicPeriod.id == PeriodGrade.academic_period_id)
        .join(Institution, Institution.id == AcademicPeriod.institution_id)
        .where(
            Institution.name == ALNOOR_SPEC.institution_name,
            Subject.code == subject_code,
            Grade.name == grade_name,
            Topic.slug == slug,
        )
    )
    assert topic is not None
    return topic


def test_ambiguous_slug_404_and_topic_quiz_is_precise(client: TestClient, clean_db: str) -> None:
    """English and Arabic both use topic slug ``grammar``; the slug path must not pick one."""
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        generate_school(session, ALNOOR_SPEC)
        session.commit()
        eng_grammar = _alnoor_topic(
            session, subject_code="ENG", grade_name="Grade 8", slug="grammar"
        )
        arb_grammar = _alnoor_topic(
            session, subject_code="ARB", grade_name="Grade 8", slug="grammar"
        )
        assert eng_grammar.id != arb_grammar.id
        eng_grammar_id = eng_grammar.id
        arb_grammar_id = arb_grammar.id
        eng_child_id = session.scalar(
            select(Subtopic.id)
            .where(Subtopic.topic_id == eng_grammar.id)
            .order_by(Subtopic.sequence)
        )
        arb_child_id = session.scalar(
            select(Subtopic.id)
            .where(Subtopic.topic_id == arb_grammar.id)
            .order_by(Subtopic.sequence)
        )
    engine.dispose()

    # Grade 8 students are enrolled in every subject, so both grammar offerings are in scope.
    # With ALNOOR_SPEC sizing, students 1–12 are Grades 6–7; student13 is the first Grade 8.
    headers = _login(client, "student13@alnoor.school", "demo1234", ALNOOR_SPEC.institution_name)
    ambiguous = client.get("/api/v1/materials/grammar/quiz", headers=headers)
    assert ambiguous.status_code == 404, ambiguous.text
    assert client.get(f"/api/v1/subtopics/{eng_child_id}/quiz", headers=headers).status_code == 404
    assert client.get(f"/api/v1/subtopics/{arb_child_id}/quiz", headers=headers).status_code == 404

    directory = client.get("/api/v1/me/learning-directory", headers=headers)
    assert directory.status_code == 200, directory.text

    def topic_quiz(subject_name: str) -> dict[str, object]:
        subject = next(
            row
            for row in directory.json()["subjects"]
            if row["grade_name"] == "Grade 8" and row["name"] == subject_name
        )
        return next(item for item in subject["topics"] if item["slug"] == "grammar")

    eng_topic = topic_quiz("English")
    arb_topic = topic_quiz("Arabic")
    assert eng_topic["id"] == str(eng_grammar_id)
    assert arb_topic["id"] == str(arb_grammar_id)
    assert eng_topic["overall_quiz"]["id"] != arb_topic["overall_quiz"]["id"]
    assert "Grammar" in str(eng_topic["overall_quiz"]["title"])
    assert "Grammar" in str(arb_topic["overall_quiz"]["title"])

    eng_lesson = client.get(f"/api/v1/topics/{eng_grammar_id}/material", headers=headers)
    arb_lesson = client.get(f"/api/v1/topics/{arb_grammar_id}/material", headers=headers)
    assert eng_lesson.status_code == 200, eng_lesson.text
    assert arb_lesson.status_code == 200, arb_lesson.text
    assert eng_lesson.json()["quiz_id"] == eng_topic["overall_quiz"]["id"]
    assert arb_lesson.json()["quiz_id"] == arb_topic["overall_quiz"]["id"]
    assert "correct_option_label" not in str(eng_lesson.json())
    assert "correct_option_label" not in str(arb_lesson.json())

    admin = _login(
        client, "fatima.almansouri@alnoor.school", "demo1234", ALNOOR_SPEC.institution_name
    )
    assert client.get("/api/v1/materials/grammar/quiz", headers=admin).status_code == 404
    assert client.get(f"/api/v1/topics/{eng_grammar_id}/material", headers=admin).status_code == 200
