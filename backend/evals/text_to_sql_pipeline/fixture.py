"""Golden Bench School: a small deterministic teacher scope for the text-to-SQL bench.

``compare.py --live --load-fixture`` loads this school into ``DATABASE_URL`` and
sets the bench teacher from the row it just wrote. The wipe removes only this
institution. It does not truncate the rest of the database.

The asking teacher is Meera Krishnan (``meera.bench@golden.school``, password
``demo1234``). She teaches Grade 6 section 6A Mathematics and Science. Noura
Al Hashimi teaches section 6B. Meera's queries must not return 6B students.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from education_platform.db import models as _models
from education_platform.db.session import sync_session
from education_platform.modules.academics.models import (
    AcademicPeriod,
    AcademicPeriodStatus,
    EnrollmentStatus,
    Grade,
    GradeSubjectOffering,
    LearningOutcome,
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
    AttemptAnswer,
    AttemptQuestion,
    CommonMasteryQuiz,
    FeedbackSummaryJob,
    Question,
    QuestionAnswerKey,
    QuestionOption,
    QuestionOutcomeTag,
    QuestionVersion,
    QuizAttempt,
    QuizAttemptStatus,
    QuizItem,
    QuizMaterialBinding,
    QuizRelease,
    QuizVersion,
    StudentSubtopicGoal,
    SubjectFeedbackSummary,
)
from education_platform.modules.assistant.models import ChatConversation, ChatMessage
from education_platform.modules.at_risk.models import AtRiskFlag
from education_platform.modules.attendance.models import AttendanceRecord, AttendanceStatus
from education_platform.modules.audit.models import AuditEvent
from education_platform.modules.auth.models import (
    Institution,
    InstitutionStatus,
    RefreshSession,
    RoleName,
    StudentProfile,
    StudentProfileStatus,
    User,
    UserRole,
    UserStatus,
)
from education_platform.modules.auth.security import hash_password
from education_platform.modules.engagement.models import SlideEngagement
from education_platform.modules.materials.models import (
    SourceChunk,
    SourceMaterial,
    SourceMaterialVersion,
    StudentMaterialProgress,
)
from education_platform.modules.self_evaluation.models import SelfEvaluation
from education_platform.modules.synthetic.generator import DEFAULT_PASSWORD
from education_platform.modules.synthetic.quizzes import ensure_topic_mastery_quiz

_ = _models

INSTITUTION_NAME = "Golden Bench School"
TEACHER_EMAIL = "meera.bench@golden.school"
OTHER_TEACHER_EMAIL = "noura.bench@golden.school"
PASSWORD = DEFAULT_PASSWORD

_PASS = Decimal("70.00")


@dataclass(frozen=True, slots=True)
class BenchSchool:
    institution_id: UUID
    teacher_user_id: UUID
    other_teacher_user_id: UUID


def load_golden_bench() -> BenchSchool:
    """Replace Golden Bench School in the current ``DATABASE_URL`` and commit."""
    session = sync_session()
    try:
        school = seed_school(session)
        session.commit()
        return school
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def seed_school(session: Session) -> BenchSchool:
    existing = session.scalar(select(Institution.id).where(Institution.name == INSTITUTION_NAME))
    if existing is not None:
        _wipe(session, existing)
        session.flush()
    return _build(session)


def _build(session: Session) -> BenchSchool:
    password_hash = hash_password(PASSWORD)
    institution = Institution(
        name=INSTITUTION_NAME,
        timezone="UTC",
        status=InstitutionStatus.ACTIVE,
    )
    session.add(institution)
    session.flush()

    meera = _user(
        session,
        institution.id,
        TEACHER_EMAIL,
        "Meera Krishnan",
        RoleName.TEACHER,
        password_hash,
    )
    noura = _user(
        session,
        institution.id,
        OTHER_TEACHER_EMAIL,
        "Noura Al Hashimi",
        RoleName.TEACHER,
        password_hash,
    )
    grade = Grade(institution_id=institution.id, name="Grade 6", sort_order=6)
    mathematics = Subject(institution_id=institution.id, name="Mathematics", code="MATH")
    science = Subject(institution_id=institution.id, name="Science", code="SCI")
    period = AcademicPeriod(
        institution_id=institution.id,
        name="Term 1 2026",
        start_date=date(2026, 1, 5),
        end_date=date(2026, 3, 27),
        status=AcademicPeriodStatus.ACTIVE,
    )
    session.add_all([grade, mathematics, science, period])
    session.flush()
    period_grade = PeriodGrade(academic_period_id=period.id, grade_id=grade.id)
    session.add(period_grade)
    session.flush()
    section_a = Section(period_grade_id=period_grade.id, name="6A")
    section_b = Section(period_grade_id=period_grade.id, name="6B")
    math_offering = GradeSubjectOffering(period_grade_id=period_grade.id, subject_id=mathematics.id)
    science_offering = GradeSubjectOffering(period_grade_id=period_grade.id, subject_id=science.id)
    session.add_all([section_a, section_b, math_offering, science_offering])
    session.flush()

    fractions = _topic(session, math_offering, "Fractions", "fractions", 1, "Equivalent fractions")
    decimals = _topic(session, math_offering, "Decimals", "decimals", 2, "Place value")
    plants = _topic(session, science_offering, "Plants", "plants", 1, "Photosynthesis")
    window_start = datetime(2026, 1, 5, tzinfo=UTC)
    window_end = datetime(2026, 3, 27, 23, tzinfo=UTC)
    fraction_quiz, _items = ensure_topic_mastery_quiz(
        session,
        fractions,
        _subtopics(session, fractions.id),
        starts_at=window_start,
        ends_at=window_end,
        released_by_user_id=meera.id,
    )
    decimal_quiz, _items = ensure_topic_mastery_quiz(
        session,
        decimals,
        _subtopics(session, decimals.id),
        starts_at=window_start,
        ends_at=window_end,
        released_by_user_id=meera.id,
    )
    plant_quiz, _items = ensure_topic_mastery_quiz(
        session,
        plants,
        _subtopics(session, plants.id),
        starts_at=window_start,
        ends_at=window_end,
        released_by_user_id=meera.id,
    )

    for teacher, section in ((meera, section_a), (noura, section_b)):
        for offering in (math_offering, science_offering):
            session.add(
                TeachingAssignment(
                    teacher_user_id=teacher.id,
                    academic_period_id=period.id,
                    grade_subject_offering_id=offering.id,
                    section_id=section.id,
                    status=TeachingAssignmentStatus.ACTIVE,
                )
            )
    session.flush()

    students = {
        "6A": ("Aisha Rahman", "Omar Haddad", "Lina Farouk"),
        "6B": ("Yusuf Ali", "Hana Saleh"),
    }
    sections = {"6A": section_a, "6B": section_b}
    enrollments: dict[tuple[str, str], StudentSubjectEnrollment] = {}
    for section_name, names in students.items():
        for index, full_name in enumerate(names, start=1):
            user = _user(
                session,
                institution.id,
                f"{full_name.split()[0].lower()}.bench@golden.school",
                full_name,
                RoleName.STUDENT,
                password_hash,
            )
            profile = StudentProfile(
                institution_id=institution.id,
                user_id=user.id,
                student_identifier=f"GB-{section_name}-{index:02d}",
                full_name=full_name,
                status=StudentProfileStatus.ACTIVE,
            )
            session.add(profile)
            session.flush()
            grade_enrollment = StudentGradeEnrollment(
                student_id=profile.id,
                academic_period_id=period.id,
                period_grade_id=period_grade.id,
                section_id=sections[section_name].id,
                status=EnrollmentStatus.ACTIVE,
            )
            session.add(grade_enrollment)
            session.flush()
            for offering, subject_name in (
                (math_offering, "Mathematics"),
                (science_offering, "Science"),
            ):
                subject_enrollment = StudentSubjectEnrollment(
                    student_id=profile.id,
                    grade_enrollment_id=grade_enrollment.id,
                    grade_subject_offering_id=offering.id,
                    status=EnrollmentStatus.ACTIVE,
                )
                session.add(subject_enrollment)
                session.flush()
                enrollments[(full_name, subject_name)] = subject_enrollment
            _attendance(session, profile.id, period.id, sections[section_name].id, full_name)

    quizzes = {
        ("Mathematics", "Fractions"): fraction_quiz,
        ("Mathematics", "Decimals"): decimal_quiz,
        ("Science", "Plants"): plant_quiz,
    }
    for full_name, subject_name, topic_name, attempt_number, score, submitted in _ATTEMPTS:
        _attempt(
            session,
            student_id=enrollments[(full_name, subject_name)].student_id,
            enrollment=enrollments[(full_name, subject_name)],
            quiz=quizzes[(subject_name, topic_name)],
            attempt_number=attempt_number,
            score=score,
            submitted=submitted,
        )
    session.flush()
    return BenchSchool(
        institution_id=institution.id,
        teacher_user_id=meera.id,
        other_teacher_user_id=noura.id,
    )


def _user(
    session: Session,
    institution_id: UUID,
    email: str,
    full_name: str,
    role: RoleName,
    password_hash: str,
) -> User:
    user = User(
        institution_id=institution_id,
        email=email,
        full_name=full_name,
        password_hash=password_hash,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    session.flush()
    session.add(UserRole(user_id=user.id, role=role))
    return user


def _topic(
    session: Session,
    offering: GradeSubjectOffering,
    name: str,
    slug: str,
    sequence: int,
    subtopic_name: str,
) -> Topic:
    topic = Topic(
        grade_subject_offering_id=offering.id,
        name=name,
        slug=slug,
        sequence=sequence,
    )
    session.add(topic)
    session.flush()
    subtopic = Subtopic(
        topic_id=topic.id,
        name=subtopic_name,
        slug=slug + "-tag",
        sequence=1,
    )
    session.add(subtopic)
    session.flush()
    session.add(
        LearningOutcome(
            subtopic_id=subtopic.id,
            code=slug.upper()[:8],
            statement=f"Students can explain {subtopic_name.lower()}.",
            sequence=1,
        )
    )
    session.flush()
    return topic


def _subtopics(session: Session, topic_id: UUID) -> list[Subtopic]:
    return list(session.scalars(select(Subtopic).where(Subtopic.topic_id == topic_id)))


def _attendance(
    session: Session,
    student_id: UUID,
    period_id: UUID,
    section_id: UUID,
    full_name: str,
) -> None:
    for offset, status_name in enumerate(_ATTENDANCE[full_name]):
        session.add(
            AttendanceRecord(
                student_id=student_id,
                academic_period_id=period_id,
                section_id=section_id,
                grade_subject_offering_id=None,
                on_date=date(2026, 1, 5 + offset),
                status=AttendanceStatus(status_name),
            )
        )


def _attempt(
    session: Session,
    *,
    student_id: UUID,
    enrollment: StudentSubjectEnrollment,
    quiz: QuizVersion,
    attempt_number: int,
    score: str,
    submitted: str,
) -> None:
    percent = Decimal(score)
    moment = datetime.fromisoformat(submitted).replace(tzinfo=UTC)
    session.add(
        QuizAttempt(
            student_id=student_id,
            student_subject_enrollment_id=enrollment.id,
            quiz_version_id=quiz.id,
            attempt_number=attempt_number,
            status=QuizAttemptStatus.RELEASED,
            started_at=moment,
            submitted_at=moment,
            scored_at=moment,
            score_raw=percent,
            score_percent=percent,
            pass_threshold_percent=_PASS,
            passed=percent >= _PASS,
        )
    )


def _wipe(session: Session, institution_id: UUID) -> None:
    student_ids = list(
        session.scalars(
            select(StudentProfile.id).where(StudentProfile.institution_id == institution_id)
        )
    )
    user_ids = list(session.scalars(select(User.id).where(User.institution_id == institution_id)))
    period_ids = list(
        session.scalars(
            select(AcademicPeriod.id).where(AcademicPeriod.institution_id == institution_id)
        )
    )
    period_grade_ids = _ids(
        session,
        select(PeriodGrade.id).where(PeriodGrade.academic_period_id.in_(period_ids)),
        period_ids,
    )
    offering_ids = _ids(
        session,
        select(GradeSubjectOffering.id).where(
            GradeSubjectOffering.period_grade_id.in_(period_grade_ids)
        ),
        period_grade_ids,
    )
    topic_ids = _ids(
        session,
        select(Topic.id).where(Topic.grade_subject_offering_id.in_(offering_ids)),
        offering_ids,
    )
    subtopic_ids = _ids(
        session, select(Subtopic.id).where(Subtopic.topic_id.in_(topic_ids)), topic_ids
    )
    question_ids = _ids(
        session, select(Question.id).where(Question.subtopic_id.in_(subtopic_ids)), subtopic_ids
    )
    question_version_ids = _ids(
        session,
        select(QuestionVersion.id).where(QuestionVersion.question_id.in_(question_ids)),
        question_ids,
    )
    quiz_ids = _ids(
        session,
        select(CommonMasteryQuiz.id).where(CommonMasteryQuiz.topic_id.in_(topic_ids)),
        topic_ids,
    )
    quiz_version_ids = _ids(
        session, select(QuizVersion.id).where(QuizVersion.quiz_id.in_(quiz_ids)), quiz_ids
    )
    attempt_ids = _ids(
        session,
        select(QuizAttempt.id).where(QuizAttempt.student_id.in_(student_ids)),
        student_ids,
    )
    enrollment_ids = _ids(
        session,
        select(StudentSubjectEnrollment.id).where(
            StudentSubjectEnrollment.student_id.in_(student_ids)
        ),
        student_ids,
    )
    material_ids = _ids(
        session,
        select(SourceMaterial.id).where(SourceMaterial.topic_id.in_(topic_ids)),
        topic_ids,
    )
    material_version_ids = _ids(
        session,
        select(SourceMaterialVersion.id).where(
            SourceMaterialVersion.source_material_id.in_(material_ids)
        ),
        material_ids,
    )
    conversation_ids = _ids(
        session,
        select(ChatConversation.id).where(ChatConversation.institution_id == institution_id),
        [institution_id],
    )

    _drop(session, AttemptAnswer, AttemptAnswer.attempt_id, attempt_ids)
    _drop(session, AttemptQuestion, AttemptQuestion.attempt_id, attempt_ids)
    _drop(session, QuizAttempt, QuizAttempt.id, attempt_ids)
    _drop(session, QuizItem, QuizItem.quiz_version_id, quiz_version_ids)
    _drop(session, QuizMaterialBinding, QuizMaterialBinding.quiz_version_id, quiz_version_ids)
    _drop(session, QuizRelease, QuizRelease.quiz_version_id, quiz_version_ids)
    _drop(session, QuestionOutcomeTag, QuestionOutcomeTag.question_version_id, question_version_ids)
    _drop(session, QuestionAnswerKey, QuestionAnswerKey.question_version_id, question_version_ids)
    _drop(session, QuestionOption, QuestionOption.question_version_id, question_version_ids)
    _drop(session, QuizVersion, QuizVersion.id, quiz_version_ids)
    _drop(session, QuestionVersion, QuestionVersion.id, question_version_ids)
    _drop(session, Question, Question.id, question_ids)
    _drop(session, CommonMasteryQuiz, CommonMasteryQuiz.id, quiz_ids)
    _drop(session, SourceChunk, SourceChunk.source_material_version_id, material_version_ids)
    _drop(session, SourceMaterialVersion, SourceMaterialVersion.id, material_version_ids)
    _drop(session, SourceMaterial, SourceMaterial.id, material_ids)
    _drop(session, LearningOutcome, LearningOutcome.subtopic_id, subtopic_ids)
    _drop(session, SlideEngagement, SlideEngagement.student_id, student_ids)
    _drop(
        session,
        StudentMaterialProgress,
        StudentMaterialProgress.student_subject_enrollment_id,
        enrollment_ids,
    )
    _drop(session, AtRiskFlag, AtRiskFlag.student_id, student_ids)
    _drop(session, AttendanceRecord, AttendanceRecord.student_id, student_ids)
    _drop(session, StudentSubtopicGoal, StudentSubtopicGoal.student_id, student_ids)
    _drop(session, FeedbackSummaryJob, FeedbackSummaryJob.student_id, student_ids)
    _drop(session, SubjectFeedbackSummary, SubjectFeedbackSummary.student_id, student_ids)
    _drop(session, SelfEvaluation, SelfEvaluation.student_id, student_ids)
    _drop(session, StudentSubjectEnrollment, StudentSubjectEnrollment.id, enrollment_ids)
    _drop(session, StudentGradeEnrollment, StudentGradeEnrollment.student_id, student_ids)
    _drop(session, TeachingAssignment, TeachingAssignment.teacher_user_id, user_ids)
    _drop(session, Subtopic, Subtopic.id, subtopic_ids)
    _drop(session, Topic, Topic.id, topic_ids)
    _drop(session, Section, Section.period_grade_id, period_grade_ids)
    _drop(session, GradeSubjectOffering, GradeSubjectOffering.id, offering_ids)
    _drop(session, PeriodGrade, PeriodGrade.id, period_grade_ids)
    _drop(session, ChatMessage, ChatMessage.conversation_id, conversation_ids)
    _drop(session, ChatConversation, ChatConversation.id, conversation_ids)
    _drop(session, AuditEvent, AuditEvent.institution_id, [institution_id])
    _drop(session, UserRole, UserRole.user_id, user_ids)
    _drop(session, RefreshSession, RefreshSession.user_id, user_ids)
    _drop(session, StudentProfile, StudentProfile.id, student_ids)
    _drop(session, User, User.id, user_ids)
    if period_ids:
        session.execute(delete(AcademicPeriod).where(AcademicPeriod.id.in_(period_ids)))
    session.execute(delete(Grade).where(Grade.institution_id == institution_id))
    session.execute(delete(Subject).where(Subject.institution_id == institution_id))
    session.execute(delete(Institution).where(Institution.id == institution_id))
    session.expire_all()


def _ids(session: Session, statement: object, parents: list[UUID]) -> list[UUID]:
    if not parents:
        return []
    return list(session.scalars(statement))  # type: ignore[arg-type]


def _drop(session: Session, model: type, column: object, ids: list[UUID]) -> None:
    if ids:
        session.execute(delete(model).where(column.in_(ids)))  # type: ignore[attr-defined]


# name, subject, topic, attempt_number, score_percent, submitted_at (UTC)
_ATTEMPTS: tuple[tuple[str, str, str, int, str, str], ...] = (
    ("Aisha Rahman", "Mathematics", "Fractions", 1, "80.00", "2026-01-20T09:00:00"),
    ("Aisha Rahman", "Mathematics", "Fractions", 2, "60.00", "2026-02-17T09:00:00"),
    ("Aisha Rahman", "Mathematics", "Decimals", 1, "70.00", "2026-03-03T09:00:00"),
    ("Omar Haddad", "Mathematics", "Fractions", 1, "40.00", "2026-01-20T09:05:00"),
    ("Omar Haddad", "Mathematics", "Decimals", 1, "50.00", "2026-03-03T09:05:00"),
    ("Lina Farouk", "Mathematics", "Fractions", 1, "90.00", "2026-01-20T09:10:00"),
    ("Lina Farouk", "Mathematics", "Decimals", 1, "100.00", "2026-03-03T15:00:00"),
    ("Aisha Rahman", "Science", "Plants", 1, "90.00", "2026-02-10T10:00:00"),
    ("Omar Haddad", "Science", "Plants", 1, "55.00", "2026-02-10T10:05:00"),
    ("Lina Farouk", "Science", "Plants", 1, "75.00", "2026-02-10T10:10:00"),
    ("Yusuf Ali", "Mathematics", "Fractions", 1, "10.00", "2026-01-21T09:00:00"),
    ("Yusuf Ali", "Mathematics", "Decimals", 1, "30.00", "2026-03-04T09:00:00"),
    ("Hana Saleh", "Mathematics", "Fractions", 1, "20.00", "2026-01-21T09:05:00"),
    ("Hana Saleh", "Mathematics", "Decimals", 1, "40.00", "2026-03-04T09:05:00"),
    ("Yusuf Ali", "Science", "Plants", 1, "95.00", "2026-02-11T10:00:00"),
    ("Hana Saleh", "Science", "Plants", 1, "85.00", "2026-02-11T10:05:00"),
)

_ATTENDANCE: dict[str, tuple[str, ...]] = {
    "Aisha Rahman": ("present", "present", "present", "late", "absent"),
    "Omar Haddad": ("present", "absent", "absent", "absent", "late"),
    "Lina Farouk": ("present", "present", "present", "present", "present"),
    "Yusuf Ali": ("absent", "absent", "absent", "absent", "absent"),
    "Hana Saleh": ("present", "present", "present", "absent", "absent"),
}
