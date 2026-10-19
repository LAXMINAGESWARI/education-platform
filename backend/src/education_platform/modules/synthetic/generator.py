"""Build a synthetic school with the demo narrative deliberately planted in it.

CBT is providing no data, so every feature that reads student records -- dashboards,
ask-the-data, the early-warning engine -- has nothing to run against until this exists.

**Why the data is authored rather than random.** Uniformly random marks make a useless
demo: the early-warning engine flags everybody or nobody, and section comparisons look like
noise. So three things are planted on purpose:

1. A **declining student** whose quiz scores fall steadily across the term while their
   attendance drops below the eligibility threshold. This is the student the early-warning
   engine must catch, and catching them is the demo.
2. **Two sections of the same subject that genuinely differ** on one topic, so the
   section-comparison chart shows a real gap a teacher could act on.
3. **Attendance that predicts marks for some students and deliberately not for others**, so
   the engine's explanations are visibly non-trivial rather than "low attendance = flagged".

Everything is seeded from a fixed integer, so the same command always produces the same
school. Anyone can reset to a known state before a demo.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

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
    QuizAttempt,
    QuizAttemptStatus,
    QuizRelease,
)
from education_platform.modules.assistant.models import ChatConversation
from education_platform.modules.at_risk.models import AtRiskFlag
from education_platform.modules.attendance.models import AttendanceRecord, AttendanceStatus
from education_platform.modules.auth.models import (
    AuditEvent,
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
from education_platform.modules.materials.models import (
    SourceMaterialVersion,
    StudentMaterialProgress,
)
from education_platform.modules.synthetic.curriculum import concepts_for, slugify
from education_platform.modules.synthetic.lessons import (
    bind_quiz_to_lesson,
    complete_lesson,
    publish_approved_lesson,
    topic_lesson_markdown,
)
from education_platform.modules.synthetic.quizzes import ensure_topic_mastery_quiz

DEFAULT_PASSWORD = "demo1234"

#: The student the early-warning engine must catch. Kept unique across the school.
PLANTED_STUDENT_NAME = "Aisha Rahman"

#: Deliberately below the 75% exam-eligibility rule in Attendance Policy v3 section 2.1.
PLANTED_ATTENDANCE_RATE = 0.62

#: Low attendance, strong marks -- so attendance alone never explains a flag.
COUNTER_EXAMPLE_ATTENDANCE_RATE = 0.70

#: Every Nth student is a counter-example.
COUNTER_EXAMPLE_EVERY = 17

FIRST_NAMES = [
    "Aisha",
    "Omar",
    "Priya",
    "Yusuf",
    "Noor",
    "Karim",
    "Layla",
    "Bilal",
    "Zainab",
    "Hassan",
    "Meera",
    "Tariq",
    "Sara",
    "Imran",
    "Fatima",
    "Rohan",
    "Amina",
    "Daniyal",
    "Hina",
    "Faisal",
    "Nadia",
    "Sami",
    "Rania",
    "Adnan",
    "Leena",
    "Junaid",
    "Maryam",
    "Zaid",
    "Huda",
    "Arif",
]
LAST_NAMES = [
    "Rahman",
    "Farooq",
    "Nair",
    "Ahmed",
    "Abdullah",
    "Idris",
    "Suleiman",
    "Haq",
    "Osman",
    "Al-Balushi",
    "Krishnan",
    "Menon",
    "Sheikh",
    "Qureshi",
    "Iqbal",
    "Mirza",
    "Habib",
    "Yusuf",
]

SUBJECTS = [
    ("Mathematics", "MATH"),
    ("Science", "SCI"),
    ("English", "ENG"),
    ("Arabic", "ARB"),
    ("Social Studies", "SOC"),
]

#: Per subject: real topics (Fractions, Decimals, …). The first maths topic is the one the
#: planted section gap and the declining student both sit on. Names live in curriculum.py.


@dataclass(frozen=True, slots=True)
class SchoolSpec:
    """Size and shape of the school to build. Defaults are the demo school."""

    institution_name: str = "Al Noor International"
    grades: tuple[str, ...] = ("Grade 6", "Grade 7", "Grade 8", "Grade 9", "Grade 10")
    sections_per_grade: int = 2
    students_per_section: int = 24
    quizzes_per_subject: int = 5
    term_weeks: int = 10
    seed: int = 20260812
    #: Small enough for the test suite; override for the real demo.
    subjects: tuple[tuple[str, str], ...] = tuple(SUBJECTS)


@dataclass
class GenerationResult:
    institution_id: UUID
    students: int = 0
    teachers: int = 0
    sections: int = 0
    offerings: int = 0
    attempts: int = 0
    attendance_rows: int = 0
    lessons: int = 0
    planted: dict[str, str] = field(default_factory=dict)


def _wipe(session: Session, institution_id: UUID) -> None:
    """Remove a previously generated school so regeneration is repeatable."""
    student_ids = list(
        session.scalars(
            select(StudentProfile.id).where(StudentProfile.institution_id == institution_id)
        )
    )
    if student_ids:
        attempt_ids = list(
            session.scalars(select(QuizAttempt.id).where(QuizAttempt.student_id.in_(student_ids)))
        )
        if attempt_ids:
            session.execute(delete(AttemptAnswer).where(AttemptAnswer.attempt_id.in_(attempt_ids)))
            session.execute(delete(QuizAttempt).where(QuizAttempt.id.in_(attempt_ids)))
        enrollment_ids = list(
            session.scalars(
                select(StudentSubjectEnrollment.id).where(
                    StudentSubjectEnrollment.student_id.in_(student_ids)
                )
            )
        )
        if enrollment_ids:
            session.execute(
                delete(StudentMaterialProgress).where(
                    StudentMaterialProgress.student_subject_enrollment_id.in_(enrollment_ids)
                )
            )
        session.execute(delete(AtRiskFlag).where(AtRiskFlag.student_id.in_(student_ids)))
        session.execute(
            delete(AttendanceRecord).where(AttendanceRecord.student_id.in_(student_ids))
        )
        session.execute(
            delete(StudentSubjectEnrollment).where(
                StudentSubjectEnrollment.student_id.in_(student_ids)
            )
        )
        session.execute(
            delete(StudentGradeEnrollment).where(StudentGradeEnrollment.student_id.in_(student_ids))
        )
        session.execute(delete(StudentProfile).where(StudentProfile.id.in_(student_ids)))

    # Audit events reference users, so they must go before the users do. Regenerating is a
    # full reset of one synthetic school, and its audit history is synthetic too -- but note
    # this is the only place anything deletes audit rows, and it is deliberately scoped to a
    # single institution.
    session.execute(delete(AuditEvent).where(AuditEvent.institution_id == institution_id))

    # Users too, or a regenerate collides with the unique (institution, email) constraint.
    user_ids = list(session.scalars(select(User.id).where(User.institution_id == institution_id)))
    if user_ids:
        # Lessons and open quiz windows stay, because topics and quizzes are reused.
        # Their submitter and releaser are replaced on the next publish.
        session.execute(
            update(SourceMaterialVersion)
            .where(SourceMaterialVersion.submitted_by_user_id.in_(user_ids))
            .values(submitted_by_user_id=None)
            .execution_options(synchronize_session=False)
        )
        session.execute(
            update(QuizRelease)
            .where(QuizRelease.released_by_user_id.in_(user_ids))
            .values(released_by_user_id=None)
            .execution_options(synchronize_session=False)
        )
        session.execute(
            delete(ChatConversation).where(ChatConversation.owner_user_id.in_(user_ids))
        )
        session.execute(
            delete(TeachingAssignment).where(TeachingAssignment.teacher_user_id.in_(user_ids))
        )
        session.execute(delete(UserRole).where(UserRole.user_id.in_(user_ids)))
        session.execute(delete(RefreshSession).where(RefreshSession.user_id.in_(user_ids)))
        session.execute(delete(User).where(User.id.in_(user_ids)))
    session.flush()


def _make_user(
    session: Session, institution_id: UUID, email: str, full_name: str, role: RoleName
) -> User:
    user = User(
        institution_id=institution_id,
        email=email.lower(),
        full_name=full_name,
        password_hash=hash_password(DEFAULT_PASSWORD),
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    session.flush()
    session.add(UserRole(user_id=user.id, role=role))
    return user


def _score_for(rng: random.Random, base: float, spread: float = 9.0) -> float:
    return max(5.0, min(100.0, rng.gauss(base, spread)))


def _record_topic_answers(
    session: Session,
    attempt_id: UUID,
    question_version_ids: list[UUID],
    percent: float,
) -> None:
    """Write one answer per item so self-eval can group by diagnostic subtopic."""
    if not question_version_ids:
        return
    correct_count = max(0, min(len(question_version_ids), round(percent / 20)))
    for index, question_version_id in enumerate(question_version_ids):
        correct = index < correct_count
        session.add(
            AttemptAnswer(
                attempt_id=attempt_id,
                question_version_id=question_version_id,
                selected_option_label="A" if correct else "B",
                is_correct=correct,
                marks_awarded=Decimal("1.00") if correct else Decimal("0.00"),
            )
        )


def _attendance_pattern(rng: random.Random, days: int, rate: float) -> list[bool]:
    """Exactly `rate` of `days` present, shuffled -- never sampled.

    Sampling a coin flip per day leaves the resulting percentage to luck: a planted
    student aimed at 62% came out at 76% on one run, which put them above the 75%
    eligibility threshold and quietly broke the demo. Fixing the count makes the planted
    narrative hold on every run.
    """
    present_days = round(days * rate)
    pattern = [True] * present_days + [False] * (days - present_days)
    rng.shuffle(pattern)
    return pattern


def generate_school(session: Session, spec: SchoolSpec | None = None) -> GenerationResult:
    """Build the whole school in one transaction. Idempotent for a given institution name."""
    spec = spec or SchoolSpec()
    rng = random.Random(spec.seed)

    institution = session.scalar(
        select(Institution).where(Institution.name == spec.institution_name)
    )
    if institution is None:
        institution = Institution(name=spec.institution_name, status=InstitutionStatus.ACTIVE)
        session.add(institution)
        session.flush()
    else:
        _wipe(session, institution.id)

    result = GenerationResult(institution_id=institution.id)

    term_start = date(2026, 8, 3)
    period = session.scalar(
        select(AcademicPeriod).where(
            AcademicPeriod.institution_id == institution.id,
            AcademicPeriod.name == "Term 1 2026",
        )
    )
    if period is None:
        period = AcademicPeriod(
            institution_id=institution.id,
            name="Term 1 2026",
            start_date=term_start,
            end_date=term_start + timedelta(weeks=spec.term_weeks),
            status=AcademicPeriodStatus.ACTIVE,
        )
        session.add(period)
        session.flush()

    subjects: dict[str, Subject] = {}
    for name, code in spec.subjects:
        subject = session.scalar(
            select(Subject).where(Subject.institution_id == institution.id, Subject.code == code)
        )
        if subject is None:
            subject = Subject(institution_id=institution.id, name=name, code=code)
            session.add(subject)
            session.flush()
        subjects[name] = subject

    # ---- academic structure -------------------------------------------------
    offerings: dict[tuple[str, str], GradeSubjectOffering] = {}
    sections: dict[str, list[Section]] = {}
    offering_topics: dict[tuple[str, str], list[tuple[Topic, list[Subtopic]]]] = {}

    for order, grade_name in enumerate(spec.grades, start=6):
        grade = session.scalar(
            select(Grade).where(Grade.institution_id == institution.id, Grade.name == grade_name)
        )
        if grade is None:
            grade = Grade(institution_id=institution.id, name=grade_name, sort_order=order)
            session.add(grade)
            session.flush()

        period_grade = session.scalar(
            select(PeriodGrade).where(
                PeriodGrade.academic_period_id == period.id, PeriodGrade.grade_id == grade.id
            )
        )
        if period_grade is None:
            period_grade = PeriodGrade(academic_period_id=period.id, grade_id=grade.id)
            session.add(period_grade)
            session.flush()

        grade_sections: list[Section] = []
        for index in range(spec.sections_per_grade):
            label = f"{grade_name.split()[-1]}{chr(ord('A') + index)}"
            section = session.scalar(
                select(Section).where(
                    Section.period_grade_id == period_grade.id, Section.name == label
                )
            )
            if section is None:
                section = Section(period_grade_id=period_grade.id, name=label)
                session.add(section)
                session.flush()
            grade_sections.append(section)
            result.sections += 1
        sections[grade_name] = grade_sections

        for subject_name, subject in subjects.items():
            offering = session.scalar(
                select(GradeSubjectOffering).where(
                    GradeSubjectOffering.period_grade_id == period_grade.id,
                    GradeSubjectOffering.subject_id == subject.id,
                )
            )
            if offering is None:
                offering = GradeSubjectOffering(
                    period_grade_id=period_grade.id, subject_id=subject.id
                )
                session.add(offering)
                session.flush()
            offerings[(grade_name, subject_name)] = offering
            result.offerings += 1

            concepts = concepts_for(subject_name, spec.quizzes_per_subject)
            existing_topics = list(
                session.scalars(
                    select(Topic)
                    .where(Topic.grade_subject_offering_id == offering.id)
                    .order_by(Topic.sequence, Topic.slug)
                )
            )
            if existing_topics:
                loaded: list[tuple[Topic, list[Subtopic]]] = []
                for topic in existing_topics:
                    loaded.append(
                        (
                            topic,
                            list(
                                session.scalars(
                                    select(Subtopic)
                                    .where(Subtopic.topic_id == topic.id)
                                    .order_by(Subtopic.sequence)
                                )
                            ),
                        )
                    )
                offering_topics[(grade_name, subject_name)] = loaded
                continue

            made_topics: list[tuple[Topic, list[Subtopic]]] = []
            for topic_seq, (topic_name, subtopic_names) in enumerate(concepts, start=1):
                topic = Topic(
                    grade_subject_offering_id=offering.id,
                    name=topic_name,
                    slug=slugify(topic_name),
                    sequence=topic_seq,
                )
                session.add(topic)
                session.flush()
                made_subs: list[Subtopic] = []
                for sub_seq, subtopic_name in enumerate(subtopic_names, start=1):
                    subtopic = Subtopic(
                        topic_id=topic.id,
                        name=subtopic_name,
                        slug=slugify(subtopic_name),
                        sequence=sub_seq,
                    )
                    session.add(subtopic)
                    session.flush()
                    session.add(
                        LearningOutcome(
                            subtopic_id=subtopic.id,
                            code="LO1",
                            statement=f"Explain {subtopic_name.lower()} in {topic_name.lower()}.",
                            sequence=1,
                        )
                    )
                    made_subs.append(subtopic)
                made_topics.append((topic, made_subs))
            offering_topics[(grade_name, subject_name)] = made_topics

    # ---- teachers -----------------------------------------------------------
    # Meera teaches across two grades AND two subjects on purpose: her scope is a set of
    # (offering, section) pairs, which is what makes the permission tests meaningful.
    teachers: dict[tuple[str, str], User] = {}
    feature_teacher = _make_user(
        session,
        institution.id,
        "meera.krishnan@alnoor.school",
        "Meera Krishnan",
        RoleName.TEACHER,
    )
    result.teachers += 1
    for offering_key, section_index in (
        (("Grade 8", "Mathematics"), 0),
        (("Grade 8", "Mathematics"), 1),
        (("Grade 9", "Science"), 0),
    ):
        teachers[offering_key] = feature_teacher
        grade_name, _ = offering_key
        session.add(
            TeachingAssignment(
                teacher_user_id=feature_teacher.id,
                academic_period_id=period.id,
                grade_subject_offering_id=offerings[offering_key].id,
                section_id=sections[grade_name][section_index].id,
                status=TeachingAssignmentStatus.ACTIVE,
            )
        )

    for grade_name in spec.grades:
        for subject_name in subjects:
            if (grade_name, subject_name) in {
                ("Grade 8", "Mathematics"),
                ("Grade 9", "Science"),
            }:
                continue
            handle = f"{subject_name[:3]}.{grade_name.split()[-1]}".lower()
            teacher = _make_user(
                session,
                institution.id,
                f"{handle}@alnoor.school",
                f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
                RoleName.TEACHER,
            )
            result.teachers += 1
            teachers[(grade_name, subject_name)] = teacher
            for section in sections[grade_name]:
                session.add(
                    TeachingAssignment(
                        teacher_user_id=teacher.id,
                        academic_period_id=period.id,
                        grade_subject_offering_id=offerings[(grade_name, subject_name)].id,
                        section_id=section.id,
                        status=TeachingAssignmentStatus.ACTIVE,
                    )
                )

    admin = _make_user(
        session,
        institution.id,
        "fatima.almansouri@alnoor.school",
        "Fatima Al-Mansouri",
        RoleName.ADMINISTRATOR,
    )
    _ = admin
    session.flush()

    term_end = term_start + timedelta(weeks=spec.term_weeks)
    quiz_window_start = datetime.combine(term_start, time.min, tzinfo=UTC)
    quiz_window_end = datetime.combine(term_end + timedelta(days=365), time(23, 59), tzinfo=UTC)

    # ---- quizzes and the one topic lesson ----------------------------------
    # Academic structure survives a wipe, so reuse an existing topic quiz.
    quiz_versions: dict[UUID, UUID] = {}
    quiz_question_versions: dict[UUID, list[UUID]] = {}
    topic_lessons: dict[UUID, UUID] = {}
    for key, pairs in offering_topics.items():
        grade_name, subject_name = key
        teacher = teachers[key]
        for topic, topic_subtopics in pairs:
            version, question_version_ids = ensure_topic_mastery_quiz(
                session,
                topic,
                topic_subtopics,
                starts_at=quiz_window_start,
                ends_at=quiz_window_end,
                released_by_user_id=admin.id,
            )
            quiz_versions[topic.id] = version.id
            quiz_question_versions[topic.id] = question_version_ids
            idea_names = tuple(subtopic.name for subtopic in topic_subtopics)
            lesson_version = publish_approved_lesson(
                session,
                topic=topic,
                title=topic.name,
                markdown=topic_lesson_markdown(
                    seed=spec.seed,
                    grade_name=grade_name,
                    subject_name=subject_name,
                    title=topic.name,
                    subtopics=idea_names,
                ),
                submitted_by_user_id=teacher.id,
            )
            topic_lessons[topic.id] = lesson_version.id
            bind_quiz_to_lesson(session, version.id, lesson_version.id)
            result.lessons += 1
    session.flush()

    # ---- students, marks, attendance ---------------------------------------
    planted_student_id: UUID | None = None
    counter = 0

    for grade_name in spec.grades:
        for section_index, section in enumerate(sections[grade_name]):
            for seat in range(spec.students_per_section):
                counter += 1
                is_planted = grade_name == "Grade 8" and section_index == 0 and seat == 0

                if is_planted:
                    full_name = PLANTED_STUDENT_NAME
                else:
                    # Never collide with the planted name -- "Aisha" and "Rahman" are both
                    # in the pools, and a second Aisha Rahman makes the demo ambiguous.
                    while True:
                        full_name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
                        if full_name != PLANTED_STUDENT_NAME:
                            break

                identifier = f"S-{counter:05d}"
                student_user = _make_user(
                    session,
                    institution.id,
                    f"student{counter}@alnoor.school",
                    full_name,
                    RoleName.STUDENT,
                )
                profile = StudentProfile(
                    institution_id=institution.id,
                    user_id=student_user.id,
                    student_identifier=identifier,
                    full_name=full_name,
                    status=StudentProfileStatus.ACTIVE,
                )
                session.add(profile)
                session.flush()
                result.students += 1

                if is_planted:
                    planted_student_id = profile.id
                    result.planted["declining_student"] = f"{full_name} ({identifier})"

                period_grade_id = session.scalar(
                    select(Section.period_grade_id).where(Section.id == section.id)
                )
                grade_enrollment = StudentGradeEnrollment(
                    student_id=profile.id,
                    academic_period_id=period.id,
                    period_grade_id=period_grade_id,
                    section_id=section.id,
                    status=EnrollmentStatus.ACTIVE,
                )
                session.add(grade_enrollment)
                session.flush()

                # Attendance. Most students sit high; the planted student falls below the
                # eligibility line; a deliberate minority have poor attendance but fine
                # marks, so attendance alone never explains a flag.
                if is_planted:
                    attendance_rate = PLANTED_ATTENDANCE_RATE
                elif counter % COUNTER_EXAMPLE_EVERY == 0:
                    attendance_rate = COUNTER_EXAMPLE_ATTENDANCE_RATE
                else:
                    attendance_rate = rng.uniform(0.86, 0.99)

                school_days = spec.term_weeks * 5
                pattern = _attendance_pattern(rng, school_days, attendance_rate)
                for day_offset in range(school_days):
                    on_date = term_start + timedelta(days=day_offset + (day_offset // 5) * 2)
                    present = pattern[day_offset]
                    session.add(
                        AttendanceRecord(
                            student_id=profile.id,
                            academic_period_id=period.id,
                            section_id=section.id,
                            on_date=on_date,
                            status=AttendanceStatus.PRESENT if present else AttendanceStatus.ABSENT,
                        )
                    )
                    result.attendance_rows += 1

                for subject_name in subjects:
                    offering = offerings[(grade_name, subject_name)]
                    subject_enrollment = StudentSubjectEnrollment(
                        student_id=profile.id,
                        grade_enrollment_id=grade_enrollment.id,
                        grade_subject_offering_id=offering.id,
                        status=EnrollmentStatus.ACTIVE,
                    )
                    session.add(subject_enrollment)
                    session.flush()

                    base = rng.uniform(58, 84)
                    if counter % COUNTER_EXAMPLE_EVERY == 0:
                        base = rng.uniform(72, 86)  # the counter-example students do fine

                    topic_pairs = offering_topics[(grade_name, subject_name)]
                    for order, (topic, _topic_subtopics) in enumerate(topic_pairs):
                        version_id = quiz_versions[topic.id]
                        question_version_ids = quiz_question_versions[topic.id]

                        if is_planted and subject_name == "Mathematics":
                            # Planted: a clear, steady decline across the term.
                            percent = max(28.0, 74.0 - order * 9.0)
                        elif (
                            subject_name == "Mathematics"
                            and grade_name == "Grade 8"
                            and topic.name == "Fractions"
                        ):
                            # Planted: section A is genuinely weaker on Fractions than B.
                            percent = _score_for(rng, 58.0 if section_index == 0 else 71.0)
                        else:
                            percent = _score_for(rng, base)

                        submitted = datetime.now(UTC) - timedelta(
                            days=(len(topic_pairs) - order) * 7
                        )
                        attempt = QuizAttempt(
                            student_id=profile.id,
                            student_subject_enrollment_id=subject_enrollment.id,
                            quiz_version_id=version_id,
                            attempt_number=1,
                            status=QuizAttemptStatus.RELEASED,
                            started_at=submitted - timedelta(minutes=20),
                            submitted_at=submitted,
                            scored_at=submitted,
                            score_raw=Decimal(str(round(percent / 20, 2))),
                            score_percent=Decimal(str(round(percent, 2))),
                            pass_threshold_percent=Decimal("70.00"),
                            passed=percent >= 70,
                        )
                        session.add(attempt)
                        session.flush()
                        _record_topic_answers(session, attempt.id, question_version_ids, percent)
                        result.attempts += 1
                        complete_lesson(
                            session,
                            enrollment_id=subject_enrollment.id,
                            version_id=topic_lessons[topic.id],
                            completed_at=submitted - timedelta(minutes=30),
                        )

                if counter % 40 == 0:
                    session.flush()

    session.flush()

    result.planted["section_gap"] = (
        "Grade 8 section A averages ~13 points below B on the Fractions topic quiz"
    )
    result.planted["attendance_counter_examples"] = (
        "every 17th student has ~70% attendance but strong marks, so attendance alone "
        "never explains a flag"
    )
    if planted_student_id is not None:
        result.planted["declining_student_id"] = str(planted_student_id)

    return result
