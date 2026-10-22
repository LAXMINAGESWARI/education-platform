"""Heading subtopics merge into a concept without dropping attempts."""

import importlib.util
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.modules.academics.models import (
    GradeSubjectOffering,
    LearningOutcome,
    Subject,
    Subtopic,
    Topic,
)
from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    Question,
    QuestionOutcomeTag,
    QuestionType,
    QuestionVersion,
    QuizAttempt,
    QuizAttemptStatus,
    QuizScope,
    QuizVersion,
)
from education_platform.modules.auth.models import StudentProfile, User
from education_platform.modules.generation.models import CurriculumGenerationJob
from education_platform.modules.generation.types import GenerationJobStatus
from education_platform.modules.materials.models import SourceMaterial
from education_platform.modules.materials.noise_subtopics import (
    SubtopicRef,
    cleanup_noise_subtopics,
    merge_plan,
)

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cleanup_noise_subtopics.py"
_spec = importlib.util.spec_from_file_location("cleanup_noise_subtopics_cli", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cli)


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    assert url, "the test database url is set by the fixture"
    return url


def _cli_args(*extra: str) -> list[str]:
    return ["--database-url", _database_url(), *extra]


def _attempt_count(session: Session) -> int:
    counted = session.scalar(select(func.count()).select_from(QuizAttempt))
    return int(counted or 0)


def _orphaned_attempts(session: Session) -> int:
    counted = session.scalar(
        select(func.count())
        .select_from(QuizAttempt)
        .where(QuizAttempt.quiz_version_id.not_in(select(QuizVersion.id)))
    )
    return int(counted or 0)


def test_merge_plan_folds_headings_into_the_nearest_concept() -> None:
    topic = UUID(int=1)
    concept = UUID(int=2)
    exercise = UUID(int=3)
    discussed = UUID(int=4)
    opening = UUID(int=5)
    only = UUID(int=6)
    pairs, skipped = merge_plan(
        [
            SubtopicRef(opening, topic, "TRY THESE", 1),
            SubtopicRef(concept, topic, "Area of a trapezium", 2),
            SubtopicRef(exercise, topic, "EXERCISE 9.2", 3),
            SubtopicRef(discussed, topic, "WHAT HAVE WE DISCUSSED?", 4),
            SubtopicRef(only, UUID(int=9), "Summary", 1),
        ]
    )
    assert pairs == [(opening, concept), (exercise, concept), (discussed, concept)]
    assert skipped == [only]
    assert merge_plan([SubtopicRef(concept, topic, "Area of a trapezium", 1)]) == ([], [])


def test_cleanup_dry_run_then_apply_is_idempotent(
    seeded_db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    topic = seeded_db.scalar(select(Topic).limit(1))
    assert topic is not None
    profile = seeded_db.scalar(select(StudentProfile).limit(1))
    assert profile is not None
    max_sequence = seeded_db.scalar(
        select(func.max(Subtopic.sequence)).where(Subtopic.topic_id == topic.id)
    )
    start = int(max_sequence or 0) + 1
    concept = Subtopic(
        topic_id=topic.id,
        name="Area of a trapezium",
        slug="area-of-a-trapezium-cleanup",
        sequence=start,
    )
    exercise = Subtopic(
        topic_id=topic.id,
        name="EXERCISE 9.2",
        slug="exercise-9-2-cleanup",
        sequence=start + 1,
    )
    discussed = Subtopic(
        topic_id=topic.id,
        name="WHAT HAVE WE DISCUSSED?",
        slug="what-have-we-discussed-cleanup",
        sequence=start + 2,
    )
    seeded_db.add_all([concept, exercise, discussed])
    seeded_db.flush()
    question = Question(subtopic_id=exercise.id, code="EX-9-2")
    outcome = LearningOutcome(
        subtopic_id=discussed.id,
        code="LO1",
        statement="Recall what the chapter discussed.",
        sequence=1,
    )
    quiz = CommonMasteryQuiz(
        quiz_scope=QuizScope.SUBTOPIC_MASTERY,
        subtopic_id=exercise.id,
        title="Exercise 9.2",
    )
    seeded_db.add_all([question, outcome, quiz])
    seeded_db.flush()
    version = QuizVersion(quiz_id=quiz.id, version_number=1)
    seeded_db.add(version)
    seeded_db.flush()
    attempt = QuizAttempt(
        student_id=profile.id,
        quiz_version_id=version.id,
        attempt_number=1,
        status=QuizAttemptStatus.RELEASED,
        started_at=datetime(2026, 8, 1, tzinfo=UTC),
        submitted_at=datetime(2026, 8, 1, 1, tzinfo=UTC),
        score_percent=Decimal("20.00"),
        pass_threshold_percent=Decimal("70.00"),
        passed=False,
    )
    seeded_db.add(attempt)
    seeded_db.commit()

    get_settings.cache_clear()
    assert _cli.main(_cli_args()) == 0
    script_out = capsys.readouterr().out
    assert "mode: dry-run" in script_out
    assert "noise_subtopics:" in script_out
    assert "questions_moved:" in script_out
    assert "EXERCISE 9.2 -> Area of a trapezium (" in script_out
    assert seeded_db.get(Question, question.id).subtopic_id == exercise.id

    preview = cleanup_noise_subtopics(seeded_db, dry_run=True)
    assert preview.noise_subtopics >= 2
    assert preview.merged >= 2
    assert preview.questions_moved >= 1
    assert preview.outcomes_moved >= 1
    assert preview.quizzes_retargeted >= 1
    assert preview.attempts_preserved >= 1
    printed = preview.render()
    assert printed.startswith("noise_subtopics:")
    assert f"merged: {preview.merged}" in printed
    assert f"attempts_preserved: {preview.attempts_preserved}" in printed
    seeded_db.rollback()
    assert seeded_db.get(Question, question.id).subtopic_id == exercise.id
    assert seeded_db.get(QuizAttempt, attempt.id) is not None

    applied = cleanup_noise_subtopics(seeded_db, dry_run=False)
    seeded_db.commit()
    assert applied.merged >= 2
    assert seeded_db.get(Subtopic, exercise.id) is None
    assert seeded_db.get(Subtopic, discussed.id) is None
    moved = seeded_db.get(Question, question.id)
    assert moved is not None and moved.subtopic_id == concept.id
    moved_outcome = seeded_db.get(LearningOutcome, outcome.id)
    assert moved_outcome is not None and moved_outcome.subtopic_id == concept.id
    moved_quiz = seeded_db.get(CommonMasteryQuiz, quiz.id)
    assert moved_quiz is not None and moved_quiz.subtopic_id == concept.id
    kept = seeded_db.get(QuizAttempt, attempt.id)
    assert kept is not None and kept.score_percent == Decimal("20.00")

    again = cleanup_noise_subtopics(seeded_db, dry_run=False)
    seeded_db.commit()
    assert again.merged == 0
    assert again.questions_moved == 0
    assert again.quizzes_retargeted == 0
    assert again.attempts_preserved == 0
    kept_attempts = seeded_db.scalar(
        select(func.count()).select_from(QuizAttempt).where(QuizAttempt.id == attempt.id)
    )
    assert kept_attempts == 1

    get_settings.cache_clear()
    assert _cli.main(_cli_args("--apply", "--yes")) == 0
    applied_again = capsys.readouterr().out
    assert "mode: applied" in applied_again
    assert "merged: 0" in applied_again
    assert "quizzes_retargeted: 0" in applied_again


def _add_quiz(session: Session, subtopic_id: UUID, student_id: UUID, title: str) -> QuizAttempt:
    quiz = CommonMasteryQuiz(
        quiz_scope=QuizScope.SUBTOPIC_MASTERY,
        subtopic_id=subtopic_id,
        title=title,
    )
    session.add(quiz)
    session.flush()
    version = QuizVersion(quiz_id=quiz.id, version_number=1)
    session.add(version)
    session.flush()
    attempt = QuizAttempt(
        student_id=student_id,
        quiz_version_id=version.id,
        attempt_number=1,
        status=QuizAttemptStatus.RELEASED,
        started_at=datetime(2026, 8, 2, tzinfo=UTC),
        submitted_at=datetime(2026, 8, 2, 1, tzinfo=UTC),
        score_percent=Decimal("15.00"),
        pass_threshold_percent=Decimal("70.00"),
        passed=False,
    )
    session.add(attempt)
    session.flush()
    return attempt


def test_two_quiz_headings_folding_into_one_concept_match_dry_run(
    seeded_db: Session,
) -> None:
    topic = seeded_db.scalar(select(Topic).limit(1))
    profile = seeded_db.scalar(select(StudentProfile).limit(1))
    assert topic is not None and profile is not None
    start = int(
        seeded_db.scalar(select(func.max(Subtopic.sequence)).where(Subtopic.topic_id == topic.id))
        or 0
    )
    concept = Subtopic(
        topic_id=topic.id,
        name="Area of a trapezium",
        slug="area-two-quiz-cleanup",
        sequence=start + 1,
    )
    exercise = Subtopic(
        topic_id=topic.id,
        name="EXERCISE 9.1",
        slug="exercise-9-1-two-quiz",
        sequence=start + 2,
    )
    try_these = Subtopic(
        topic_id=topic.id,
        name="TRY THESE",
        slug="try-these-two-quiz",
        sequence=start + 3,
    )
    seeded_db.add_all([concept, exercise, try_these])
    seeded_db.flush()
    exercise_question = Question(subtopic_id=exercise.id, code="EX-9-1-Q")
    try_question = Question(subtopic_id=try_these.id, code="TRY-Q")
    seeded_db.add_all([exercise_question, try_question])
    seeded_db.flush()
    exercise_attempt = _add_quiz(seeded_db, exercise.id, profile.id, "Exercise 9.1 two-quiz")
    try_attempt = _add_quiz(seeded_db, try_these.id, profile.id, "Try these two-quiz")
    seeded_db.commit()

    preview = cleanup_noise_subtopics(seeded_db, dry_run=True)
    assert seeded_db.get(Subtopic, exercise.id) is not None
    assert seeded_db.get(Question, try_question.id).subtopic_id == try_these.id
    applied = cleanup_noise_subtopics(seeded_db, dry_run=False)
    assert preview.merged == applied.merged
    assert preview.quizzes_retargeted == applied.quizzes_retargeted
    assert preview.blocked_by_quiz == applied.blocked_by_quiz
    assert preview.questions_moved == applied.questions_moved
    assert preview.attempts_preserved == applied.attempts_preserved
    assert seeded_db.get(Subtopic, exercise.id) is None
    assert seeded_db.get(Subtopic, try_these.id) is not None
    moved_quiz = seeded_db.scalar(
        select(CommonMasteryQuiz).where(CommonMasteryQuiz.title == "Exercise 9.1 two-quiz")
    )
    blocked_quiz = seeded_db.scalar(
        select(CommonMasteryQuiz).where(CommonMasteryQuiz.title == "Try these two-quiz")
    )
    assert moved_quiz is not None and moved_quiz.subtopic_id == concept.id
    assert blocked_quiz is not None and blocked_quiz.subtopic_id == try_these.id
    assert seeded_db.get(Question, exercise_question.id).subtopic_id == concept.id
    assert seeded_db.get(Question, try_question.id).subtopic_id == concept.id
    assert seeded_db.get(QuizAttempt, exercise_attempt.id) is not None
    assert seeded_db.get(QuizAttempt, try_attempt.id) is not None


def test_apply_does_not_delete_real_concept_names(seeded_db: Session) -> None:
    topic = seeded_db.scalar(select(Topic).limit(1))
    assert topic is not None
    start = int(
        seeded_db.scalar(select(func.max(Subtopic.sequence)).where(Subtopic.topic_id == topic.id))
        or 0
    )
    names = (
        "Area of a trapezium",
        "Solution of a Quadratic Equation by Factorisation",
        "Newton's Method",
        "Summary Statistics",
        "Exercise Physiology",
    )
    rows = [
        Subtopic(
            topic_id=topic.id,
            name=name,
            slug=f"real-concept-{index}",
            sequence=start + 1 + index,
        )
        for index, name in enumerate(names)
    ]
    seeded_db.add_all(rows)
    seeded_db.flush()
    questions = [
        Question(subtopic_id=row.id, code=f"REAL-{index}") for index, row in enumerate(rows)
    ]
    seeded_db.add_all(questions)
    seeded_db.commit()

    cleanup_noise_subtopics(seeded_db, dry_run=False)
    for row, question in zip(rows, questions, strict=True):
        kept = seeded_db.get(Subtopic, row.id)
        assert kept is not None and kept.name == row.name
        moved = seeded_db.get(Question, question.id)
        assert moved is not None and moved.subtopic_id == row.id


def test_subject_scope_lists_only_that_subject_and_rejects_unknown(
    seeded_db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    topic = seeded_db.scalar(select(Topic).limit(1))
    assert topic is not None
    offering = seeded_db.get(GradeSubjectOffering, topic.grade_subject_offering_id)
    assert offering is not None
    math = seeded_db.get(Subject, offering.subject_id)
    assert math is not None
    physics = Subject(institution_id=math.institution_id, name="Physics", code="PHY-NOISE")
    seeded_db.add(physics)
    seeded_db.flush()
    physics_offering = GradeSubjectOffering(
        period_grade_id=offering.period_grade_id,
        subject_id=physics.id,
    )
    seeded_db.add(physics_offering)
    seeded_db.flush()
    physics_topic = Topic(
        grade_subject_offering_id=physics_offering.id,
        name="Motion",
        slug="motion-noise",
        sequence=1,
    )
    seeded_db.add(physics_topic)
    seeded_db.flush()
    concept = Subtopic(
        topic_id=physics_topic.id,
        name="Speed",
        slug="speed",
        sequence=1,
    )
    exercise = Subtopic(
        topic_id=physics_topic.id,
        name="EXERCISE 1.1",
        slug="exercise-1-1",
        sequence=2,
    )
    seeded_db.add_all([concept, exercise])
    seeded_db.commit()

    preview = cleanup_noise_subtopics(seeded_db, dry_run=True, subject="Physics")
    assert any(
        fold.heading == "EXERCISE 1.1" and fold.target == "Speed" and fold.subject == "Physics"
        for fold in preview.folds
    )
    assert all(fold.subject == "Physics" for fold in preview.folds)
    rendered = preview.render()
    assert "EXERCISE 1.1 -> Speed (Physics / Motion)" in rendered
    assert seeded_db.get(Subtopic, exercise.id) is not None

    with pytest.raises(ValueError, match="Subject not found"):
        cleanup_noise_subtopics(seeded_db, dry_run=True, subject="No Such Subject")

    get_settings.cache_clear()
    assert _cli.main(_cli_args("--subject", str(physics.id))) == 0
    listed = capsys.readouterr().out
    assert "EXERCISE 1.1 -> Speed (Physics / Motion)" in listed
    assert "mode: dry-run" in listed

    cleanup_noise_subtopics(seeded_db, dry_run=False, subject=math.name)
    assert seeded_db.get(Subtopic, exercise.id) is not None
    cleanup_noise_subtopics(seeded_db, dry_run=False, subject=str(physics.id))
    assert seeded_db.get(Subtopic, exercise.id) is None
    assert seeded_db.get(Subtopic, concept.id) is not None


def _fresh_topic(session: Session, name: str, code: str) -> tuple[Subject, Topic, StudentProfile]:
    topic = session.scalar(select(Topic).limit(1))
    profile = session.scalar(select(StudentProfile).limit(1))
    assert topic is not None and profile is not None
    offering = session.get(GradeSubjectOffering, topic.grade_subject_offering_id)
    math = session.get(Subject, offering.subject_id) if offering is not None else None
    assert offering is not None and math is not None
    subject = Subject(institution_id=math.institution_id, name=name, code=code)
    session.add(subject)
    session.flush()
    subject_offering = GradeSubjectOffering(
        period_grade_id=offering.period_grade_id,
        subject_id=subject.id,
    )
    session.add(subject_offering)
    session.flush()
    fresh = Topic(
        grade_subject_offering_id=subject_offering.id,
        name="Motion",
        slug=f"{code.lower()}-motion",
        sequence=1,
    )
    session.add(fresh)
    session.flush()
    return subject, fresh, profile


@pytest.mark.regression
def test_two_quizzed_headings_into_one_concept_keep_attempts_and_match_counts(
    seeded_db: Session,
) -> None:
    subject, topic, profile = _fresh_topic(seeded_db, "Chemistry", "CHEM-NOISE")
    concept = Subtopic(topic_id=topic.id, name="Speed", slug="chem-speed", sequence=1)
    exercise = Subtopic(topic_id=topic.id, name="EXERCISE 1.1", slug="chem-ex-1-1", sequence=2)
    try_these = Subtopic(topic_id=topic.id, name="TRY THESE", slug="chem-try-these", sequence=3)
    seeded_db.add_all([concept, exercise, try_these])
    seeded_db.flush()
    exercise_question = Question(subtopic_id=exercise.id, code="CHEM-EX")
    try_question = Question(subtopic_id=try_these.id, code="CHEM-TRY")
    seeded_db.add_all([exercise_question, try_question])
    seeded_db.flush()
    exercise_attempt = _add_quiz(seeded_db, exercise.id, profile.id, "Chemistry exercise quiz")
    try_attempt = _add_quiz(seeded_db, try_these.id, profile.id, "Chemistry try these quiz")
    seeded_db.commit()

    before = _attempt_count(seeded_db)
    assert _orphaned_attempts(seeded_db) == 0
    preview = cleanup_noise_subtopics(seeded_db, dry_run=True, subject=str(subject.id))
    assert seeded_db.get(Subtopic, exercise.id) is not None
    assert seeded_db.get(Question, try_question.id).subtopic_id == try_these.id
    applied = cleanup_noise_subtopics(seeded_db, dry_run=False, subject=str(subject.id))
    assert preview.merged == applied.merged == 1
    assert preview.quizzes_retargeted == applied.quizzes_retargeted == 1
    assert preview.blocked_by_quiz == applied.blocked_by_quiz == 1
    assert preview.questions_moved == applied.questions_moved == 2
    assert preview.attempts_preserved == applied.attempts_preserved == 2
    assert _attempt_count(seeded_db) == before
    assert _orphaned_attempts(seeded_db) == 0
    assert seeded_db.get(QuizAttempt, exercise_attempt.id) is not None
    assert seeded_db.get(QuizAttempt, try_attempt.id) is not None
    assert seeded_db.get(Subtopic, try_these.id) is not None

    again = cleanup_noise_subtopics(seeded_db, dry_run=False, subject=str(subject.id))
    assert again.merged == 0
    assert again.questions_moved == 0
    assert again.quizzes_retargeted == 0
    assert again.blocked_by_quiz == 1
    assert again.attempts_preserved == 0
    assert _attempt_count(seeded_db) == before


@pytest.mark.regression
def test_outcome_clash_retargets_one_tag_and_drops_the_duplicate(seeded_db: Session) -> None:
    _subject, topic, _profile = _fresh_topic(seeded_db, "Biology", "BIO-NOISE")
    concept = Subtopic(topic_id=topic.id, name="Cells", slug="bio-cells", sequence=1)
    exercise = Subtopic(topic_id=topic.id, name="EXERCISE 2.1", slug="bio-ex-2-1", sequence=2)
    alone = Topic(
        grade_subject_offering_id=topic.grade_subject_offering_id,
        name="Only headings",
        slug="bio-only-headings",
        sequence=2,
    )
    seeded_db.add(alone)
    seeded_db.flush()
    lonely = Subtopic(topic_id=alone.id, name="Figure it Out", slug="bio-figure", sequence=1)
    seeded_db.add_all([concept, exercise, lonely])
    seeded_db.flush()
    kept = LearningOutcome(
        subtopic_id=concept.id, code="LO-CLASH", statement="Name a cell part.", sequence=1
    )
    moving = LearningOutcome(
        subtopic_id=exercise.id, code="LO-CLASH", statement="Name a cell part.", sequence=1
    )
    first = Question(subtopic_id=exercise.id, code="BIO-Q1")
    second = Question(subtopic_id=exercise.id, code="BIO-Q2")
    seeded_db.add_all([kept, moving, first, second])
    seeded_db.flush()
    first_version = QuestionVersion(
        question_id=first.id,
        version_number=1,
        prompt="Which part is the nucleus?",
        question_type=QuestionType.MULTIPLE_CHOICE,
        marks=Decimal("1.00"),
    )
    second_version = QuestionVersion(
        question_id=second.id,
        version_number=1,
        prompt="Which part is the membrane?",
        question_type=QuestionType.MULTIPLE_CHOICE,
        marks=Decimal("1.00"),
    )
    seeded_db.add_all([first_version, second_version])
    seeded_db.flush()
    retarget = QuestionOutcomeTag(
        question_version_id=first_version.id, learning_outcome_id=moving.id
    )
    duplicate = QuestionOutcomeTag(
        question_version_id=second_version.id, learning_outcome_id=moving.id
    )
    already = QuestionOutcomeTag(question_version_id=second_version.id, learning_outcome_id=kept.id)
    seeded_db.add_all([retarget, duplicate, already])
    seeded_db.commit()

    applied = cleanup_noise_subtopics(seeded_db, dry_run=False, subject="Biology")
    assert applied.merged == 1
    assert applied.skipped_no_sibling == 1
    assert applied.outcomes_moved == 1
    assert "Figure it Out -> (no concept)" in applied.render()
    assert seeded_db.get(LearningOutcome, moving.id) is None
    assert seeded_db.get(QuestionOutcomeTag, retarget.id).learning_outcome_id == kept.id
    assert seeded_db.get(QuestionOutcomeTag, duplicate.id) is None
    assert seeded_db.get(QuestionOutcomeTag, already.id) is not None


@pytest.mark.regression
def test_material_slug_clash_walks_the_suffix(seeded_db: Session) -> None:
    _subject, topic, _profile = _fresh_topic(seeded_db, "Geology", "GEO-NOISE")
    concept = Subtopic(topic_id=topic.id, name="Rocks", slug="geo-rocks", sequence=1)
    exercise = Subtopic(topic_id=topic.id, name="Summary", slug="geo-summary", sequence=2)
    seeded_db.add_all([concept, exercise])
    seeded_db.flush()
    seeded_db.add_all(
        [
            SourceMaterial(subtopic_id=concept.id, title="Notes", slug="notes"),
            SourceMaterial(subtopic_id=concept.id, title="Notes two", slug="notes-2"),
            SourceMaterial(subtopic_id=exercise.id, title="Notes copy", slug="notes"),
            SourceMaterial(subtopic_id=exercise.id, title="Field notes", slug="field-notes"),
        ]
    )
    seeded_db.commit()
    moving = seeded_db.scalar(
        select(SourceMaterial).where(
            SourceMaterial.subtopic_id == exercise.id, SourceMaterial.slug == "notes"
        )
    )
    assert moving is not None

    applied = cleanup_noise_subtopics(seeded_db, dry_run=False, subject="Geology")
    assert applied.materials_moved == 2
    seeded_db.refresh(moving)
    assert moving.subtopic_id == concept.id
    assert moving.slug == "notes-3"


@pytest.mark.regression
def test_in_flight_jobs_on_both_sides_block_the_delete(seeded_db: Session) -> None:
    _subject, topic, profile = _fresh_topic(seeded_db, "Astronomy", "AST-NOISE")
    user = seeded_db.scalar(select(User).limit(1))
    assert user is not None
    concept = Subtopic(topic_id=topic.id, name="Orbits", slug="ast-orbits", sequence=1)
    exercise = Subtopic(topic_id=topic.id, name="Do This", slug="ast-do-this", sequence=2)
    seeded_db.add_all([concept, exercise])
    seeded_db.flush()
    question = Question(subtopic_id=exercise.id, code="AST-Q")
    seeded_db.add(question)
    seeded_db.flush()
    attempt = _add_quiz(seeded_db, exercise.id, profile.id, "Astronomy do this quiz")
    seeded_db.add_all(
        [
            CurriculumGenerationJob(
                subtopic_id=exercise.id,
                status=GenerationJobStatus.QUEUED,
                created_by=user.id,
                round_count=0,
            ),
            CurriculumGenerationJob(
                subtopic_id=concept.id,
                status=GenerationJobStatus.RUNNING,
                created_by=user.id,
                round_count=0,
            ),
        ]
    )
    seeded_db.commit()

    applied = cleanup_noise_subtopics(seeded_db, dry_run=False, subject="Astronomy")
    assert applied.merged == 0
    assert applied.blocked_by_quiz == 1
    assert applied.questions_moved == 1
    assert applied.attempts_preserved == 1
    assert seeded_db.get(Subtopic, exercise.id) is not None
    assert seeded_db.get(Question, question.id).subtopic_id == concept.id
    assert seeded_db.get(QuizAttempt, attempt.id) is not None

    again = cleanup_noise_subtopics(seeded_db, dry_run=False, subject="Astronomy")
    assert again.attempts_preserved == 0
    assert again.questions_moved == 0
    assert again.blocked_by_quiz == 1


@pytest.mark.regression
def test_cleanup_script_refuses_without_a_target_and_without_yes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CLEANUP_DATABASE_URL", raising=False)
    assert os.environ.get("DATABASE_URL"), "the fixture sets DATABASE_URL, which must be ignored"
    assert _cli.main([]) == 2
    refused = capsys.readouterr()
    assert "CLEANUP_DATABASE_URL" in refused.err
    assert "DATABASE_URL" in refused.err

    leaked = "postgresql+asyncpg://education:s3cret-pass@127.0.0.1:5432/education_test"
    assert _cli.main(["--apply", "--database-url", leaked]) == 2
    blocked = capsys.readouterr()
    assert "s3cret-pass" not in blocked.out
    assert "s3cret-pass" not in blocked.err
    assert "host=127.0.0.1" in blocked.out
    assert "port=5432" in blocked.out
    assert "database=education_test" in blocked.out
    assert "password=***" in blocked.out
    assert "--yes" in blocked.err


def test_describe_database_target_masks_the_password() -> None:
    text = _cli.describe_database_target(
        "postgresql+asyncpg://education:s3cret-pass@db.example:6543/education_test"
    )
    assert text == "host=db.example port=6543 database=education_test password=***"
    assert "s3cret-pass" not in text


def test_typed_database_name_must_match(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = "postgresql+asyncpg://education:s3cret-pass@127.0.0.1:5432/education_test"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "other")
    assert _cli.main(["--apply", "--database-url", url]) == 2
    assert "did not match" in capsys.readouterr().err

    monkeypatch.setattr("builtins.input", lambda _prompt: _cli.database_name(_database_url()))
    assert _cli.main(_cli_args("--apply", "--subject", "No Such Subject")) == 2
    confirmed = capsys.readouterr()
    assert "Subject not found" in confirmed.err
    assert "education:education" not in confirmed.out
