"""Heading subtopics merge into a concept without dropping attempts."""

import importlib.util
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.modules.academics.models import LearningOutcome, Subtopic, Topic
from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    Question,
    QuizAttempt,
    QuizAttemptStatus,
    QuizScope,
    QuizVersion,
)
from education_platform.modules.auth.models import StudentProfile
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
    assert _cli.main([]) == 0
    script_out = capsys.readouterr().out
    assert "mode: dry-run" in script_out
    assert "noise_subtopics:" in script_out
    assert "questions_moved:" in script_out
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
    kept_attempts = seeded_db.scalar(
        select(func.count()).select_from(QuizAttempt).where(QuizAttempt.id == attempt.id)
    )
    assert kept_attempts == 1

    get_settings.cache_clear()
    assert _cli.main(["--apply"]) == 0
    applied_again = capsys.readouterr().out
    assert "mode: applied" in applied_again
    assert "merged: 0" in applied_again
    assert "quizzes_retargeted: 0" in applied_again
