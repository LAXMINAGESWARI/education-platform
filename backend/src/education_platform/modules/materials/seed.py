"""Import approved markdown curriculum from docs/curriculum into Postgres."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.db.session import create_general_sync_engine
from education_platform.db.url import to_sync_url
from education_platform.modules.academics.constants import (
    POC_GRADE_NAME,
    POC_INSTITUTION_NAME,
    POC_PERIOD_NAME,
    POC_SUBJECT_CODE,
)
from education_platform.modules.academics.models import (
    AcademicPeriod,
    EnrollmentStatus,
    Grade,
    GradeSubjectOffering,
    PeriodGrade,
    StudentGradeEnrollment,
    StudentSubjectEnrollment,
    Subject,
    Subtopic,
    Topic,
)
from education_platform.modules.assessments.models import QuizAttempt
from education_platform.modules.auth.models import (
    Institution,
    RoleName,
    StudentProfile,
    StudentProfileStatus,
    User,
    UserRole,
    UserStatus,
)
from education_platform.modules.auth.security import hash_password
from education_platform.modules.materials.markdown_parser import (
    parse_lesson,
    parse_objectives_from_lesson,
    parse_quiz,
)
from education_platform.modules.materials.models import SourceMaterialVersion
from education_platform.modules.materials.seed_content import (
    ensure_outcomes,
    upsert_material_version,
)
from education_platform.modules.materials.seed_curriculum import ensure_curriculum_root
from education_platform.modules.materials.seed_quizzes import (
    seed_topic_mastery_quiz,
    upsert_subtopic_quiz,
)

_LESSON_SUFFIX = "_lesson.md"
_QUIZ_SUFFIX = "_quiz.md"

__all__ = [
    "POC_GRADE_NAME",
    "POC_INSTITUTION_NAME",
    "POC_PERIOD_NAME",
    "POC_SUBJECT_CODE",
    "discover_topic_ids",
    "main",
    "run_curriculum_seed",
    "seed_approved_materials",
    "seed_demo_admin",
    "seed_demo_student",
]


def discover_topic_ids(materials_dir: Path) -> list[str]:
    topic_ids: set[str] = set()
    for path in materials_dir.glob("*.md"):
        name = path.name
        if name.endswith(_LESSON_SUFFIX):
            topic_ids.add(name[: -len(_LESSON_SUFFIX)])
        elif name.endswith(_QUIZ_SUFFIX):
            topic_ids.add(name[: -len(_QUIZ_SUFFIX)])
    return sorted(topic_ids)


def _seed_subtopic(
    session: Session,
    parent_topic: Topic,
    topic_id: str,
    sequence: int,
    materials_dir: Path,
) -> None:
    lesson_path = materials_dir / f"{topic_id}{_LESSON_SUFFIX}"
    quiz_path = materials_dir / f"{topic_id}{_QUIZ_SUFFIX}"
    if not lesson_path.is_file() and not quiz_path.is_file():
        return

    title_fallback = topic_id.replace("_", " ").title()
    display_title = title_fallback
    if lesson_path.is_file():
        display_title = parse_lesson(lesson_path.read_text(encoding="utf-8"), topic_id).title
    elif quiz_path.is_file():
        display_title = parse_quiz(quiz_path.read_text(encoding="utf-8"), topic_id).title

    subtopic = session.scalar(
        select(Subtopic).where(Subtopic.topic_id == parent_topic.id, Subtopic.slug == topic_id)
    )
    if subtopic is None:
        subtopic = Subtopic(
            topic_id=parent_topic.id,
            name=display_title,
            slug=topic_id,
            sequence=sequence,
        )
        session.add(subtopic)
        session.flush()
    else:
        subtopic.name = display_title
        subtopic.sequence = sequence
        session.flush()

    outcome_statements: list[str] = []
    published_material_version: SourceMaterialVersion | None = None
    if lesson_path.is_file():
        lesson = parse_lesson(lesson_path.read_text(encoding="utf-8"), topic_id)
        outcome_statements = parse_objectives_from_lesson(lesson.markdown)
        published_material_version = upsert_material_version(
            session, subtopic, title=lesson.title, markdown=lesson.markdown
        )

    outcome = ensure_outcomes(session, subtopic, outcome_statements, display_title=display_title)

    if quiz_path.is_file():
        quiz = parse_quiz(quiz_path.read_text(encoding="utf-8"), topic_id)
        upsert_subtopic_quiz(session, subtopic, outcome, quiz, published_material_version)


def _attempts_exist(session: Session) -> bool:
    return bool(session.scalar(select(QuizAttempt.id).limit(1)))


def seed_demo_student(session: Session) -> None:
    settings = get_settings()
    if not settings.is_development:
        return
    institution = session.scalar(
        select(Institution).where(Institution.name == POC_INSTITUTION_NAME)
    )
    if institution is None:
        return
    email = settings.demo_student_email.lower()
    user = session.scalar(
        select(User).where(User.institution_id == institution.id, User.email == email)
    )
    if user is None:
        user = User(
            institution_id=institution.id,
            email=email,
            full_name="Asha Student",
            password_hash=hash_password(settings.demo_student_password),
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        session.flush()
    else:
        user.status = UserStatus.ACTIVE
        user.full_name = "Asha Student"
        user.password_hash = hash_password(settings.demo_student_password)
    if (
        session.scalar(
            select(UserRole).where(UserRole.user_id == user.id, UserRole.role == RoleName.STUDENT)
        )
        is None
    ):
        session.add(UserRole(user_id=user.id, role=RoleName.STUDENT))

    profile = session.scalar(
        select(StudentProfile).where(
            StudentProfile.institution_id == institution.id,
            StudentProfile.student_identifier == "DEMO-001",
        )
    )
    if profile is None:
        profile = StudentProfile(
            institution_id=institution.id,
            user_id=user.id,
            student_identifier="DEMO-001",
            full_name="Demo Student",
            status=StudentProfileStatus.ACTIVE,
        )
        session.add(profile)
        session.flush()
    else:
        profile.user_id = user.id
        profile.status = StudentProfileStatus.ACTIVE

    period = session.scalar(
        select(AcademicPeriod).where(
            AcademicPeriod.institution_id == institution.id,
            AcademicPeriod.name == POC_PERIOD_NAME,
        )
    )
    grade = session.scalar(
        select(Grade).where(Grade.institution_id == institution.id, Grade.name == POC_GRADE_NAME)
    )
    subject = session.scalar(
        select(Subject).where(
            Subject.institution_id == institution.id, Subject.code == POC_SUBJECT_CODE
        )
    )
    if period is None or grade is None or subject is None:
        return
    period_grade = session.scalar(
        select(PeriodGrade).where(
            PeriodGrade.academic_period_id == period.id,
            PeriodGrade.grade_id == grade.id,
        )
    )
    if period_grade is None:
        return
    offering = session.scalar(
        select(GradeSubjectOffering).where(
            GradeSubjectOffering.period_grade_id == period_grade.id,
            GradeSubjectOffering.subject_id == subject.id,
        )
    )
    if offering is None:
        return
    grade_enrollment = session.scalar(
        select(StudentGradeEnrollment).where(
            StudentGradeEnrollment.student_id == profile.id,
            StudentGradeEnrollment.academic_period_id == period.id,
            StudentGradeEnrollment.status == EnrollmentStatus.ACTIVE,
        )
    )
    if grade_enrollment is None:
        grade_enrollment = StudentGradeEnrollment(
            student_id=profile.id,
            academic_period_id=period.id,
            period_grade_id=period_grade.id,
            status=EnrollmentStatus.ACTIVE,
        )
        session.add(grade_enrollment)
        session.flush()
    if (
        session.scalar(
            select(StudentSubjectEnrollment).where(
                StudentSubjectEnrollment.student_id == profile.id,
                StudentSubjectEnrollment.grade_subject_offering_id == offering.id,
                StudentSubjectEnrollment.status == EnrollmentStatus.ACTIVE,
            )
        )
        is None
    ):
        session.add(
            StudentSubjectEnrollment(
                student_id=profile.id,
                grade_enrollment_id=grade_enrollment.id,
                grade_subject_offering_id=offering.id,
                status=EnrollmentStatus.ACTIVE,
            )
        )


def seed_demo_admin(session: Session) -> None:
    settings = get_settings()
    if not settings.is_development:
        return
    institution = session.scalar(
        select(Institution).where(Institution.name == POC_INSTITUTION_NAME)
    )
    if institution is None:
        return
    email = settings.demo_admin_email.lower()
    existing = session.scalar(
        select(User).where(User.institution_id == institution.id, User.email == email)
    )
    if existing is not None:
        return
    user = User(
        institution_id=institution.id,
        email=email,
        full_name="Demo Admin",
        password_hash=hash_password(settings.demo_admin_password),
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    session.flush()
    session.add(UserRole(user_id=user.id, role=RoleName.ADMINISTRATOR))


def seed_approved_materials(
    session: Session,
    materials_dir: Path | None = None,
    *,
    replace: bool = False,
) -> list[str]:
    """Load markdown curriculum into the relational schema.

    Returns the topic ids (subtopic slugs) that were seeded.
    """
    directory = materials_dir or get_settings().materials_dir
    if not directory.is_dir():
        raise FileNotFoundError(f"Materials directory not found: {directory}")

    topic_ids = discover_topic_ids(directory)
    if not topic_ids:
        return []

    parent_topic = ensure_curriculum_root(session)

    if replace and _attempts_exist(session):
        raise RuntimeError("Cannot destructively replace seeded content after attempts exist")

    if parent_topic is None:
        seed_demo_student(session)
        seed_demo_admin(session)
        session.commit()
        return topic_ids

    for sequence, topic_id in enumerate(topic_ids, start=1):
        _seed_subtopic(session, parent_topic, topic_id, sequence, directory)
    seed_topic_mastery_quiz(session, parent_topic)
    seed_demo_student(session)
    seed_demo_admin(session)

    session.commit()
    return topic_ids


def run_curriculum_seed() -> list[str]:
    """Load ``docs/curriculum`` into Postgres. Idempotent.

    This is the work the API lifespan used to run on every boot. ``replace``
    stays false, so an existing tree is updated in place. Demo accounts are
    created only when ``ENVIRONMENT`` is development. ``SEED_SYNTHETIC`` does
    not apply; that switch belongs to the migrate script's demo school.
    """
    settings = get_settings()
    engine = create_general_sync_engine(to_sync_url(settings.database_url))
    try:
        with Session(engine) as session:
            return seed_approved_materials(session, replace=False)
    finally:
        engine.dispose()


def main() -> None:
    seeded = run_curriculum_seed()
    print(f"Seeded topics: {', '.join(seeded) if seeded else '(none)'}")


if __name__ == "__main__":
    main()
