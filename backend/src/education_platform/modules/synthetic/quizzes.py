"""Topic-mastery quizzes in the same shape publish writes.

One released quiz per topic. Each question keeps ``subtopic_id`` and an outcome tag.
There is no subtopic quiz. ``max_attempts`` stays null, matching the pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.modules.academics.models import LearningOutcome, Subtopic, Topic
from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    Question,
    QuestionAnswerKey,
    QuestionDifficulty,
    QuestionItemKind,
    QuestionOption,
    QuestionOutcomeTag,
    QuestionType,
    QuestionVersion,
    QuestionVersionStatus,
    QuizItem,
    QuizResultReleaseMode,
    QuizScope,
    QuizVersion,
    QuizVersionStatus,
)
from education_platform.modules.synthetic.lessons import ensure_open_quiz_window

TOPIC_QUIZ_ITEM_COUNT = 5

_DIFFICULTIES = (
    QuestionDifficulty.EASY,
    QuestionDifficulty.MEDIUM,
    QuestionDifficulty.HARD,
)


def ensure_topic_mastery_quiz(
    session: Session,
    topic: Topic,
    subtopics: list[Subtopic],
    *,
    starts_at: datetime,
    ends_at: datetime,
    released_by_user_id: UUID,
) -> tuple[QuizVersion, list[UUID]]:
    """Find or create the released topic quiz and return its question version ids."""
    if not subtopics:
        raise ValueError("A topic quiz needs diagnostic subtopics to tag its questions.")

    quiz = session.scalar(
        select(CommonMasteryQuiz).where(
            CommonMasteryQuiz.topic_id == topic.id,
            CommonMasteryQuiz.quiz_scope == QuizScope.TOPIC_MASTERY,
        )
    )
    if quiz is None:
        quiz = CommonMasteryQuiz(
            quiz_scope=QuizScope.TOPIC_MASTERY,
            topic_id=topic.id,
            title=f"{topic.name} topic mastery",
        )
        session.add(quiz)
        session.flush()
    else:
        quiz.title = f"{topic.name} topic mastery"

    version = session.scalar(
        select(QuizVersion)
        .where(QuizVersion.quiz_id == quiz.id)
        .order_by(QuizVersion.version_number.desc())
        .limit(1)
    )
    if version is None:
        version = QuizVersion(
            quiz_id=quiz.id,
            version_number=1,
            lifecycle_status=QuizVersionStatus.RELEASED,
            max_attempts=None,
            result_release_mode=QuizResultReleaseMode.IMMEDIATE,
            pass_threshold_percent=Decimal("70.00"),
            released_at=_now(),
        )
        session.add(version)
        session.flush()
        _add_items(session, version, topic, subtopics)
    else:
        version.lifecycle_status = QuizVersionStatus.RELEASED
        version.max_attempts = None
        version.released_at = version.released_at or _now()

    ensure_open_quiz_window(
        session,
        version,
        starts_at=starts_at,
        ends_at=ends_at,
        released_by_user_id=released_by_user_id,
    )
    question_version_ids = list(
        session.scalars(
            select(QuizItem.question_version_id)
            .where(QuizItem.quiz_version_id == version.id)
            .order_by(QuizItem.sequence)
        )
    )
    return version, question_version_ids


def _now() -> datetime:
    return datetime.now(UTC)


def _add_items(
    session: Session,
    version: QuizVersion,
    topic: Topic,
    subtopics: list[Subtopic],
) -> None:
    outcomes = {
        subtopic.id: session.scalar(
            select(LearningOutcome)
            .where(LearningOutcome.subtopic_id == subtopic.id)
            .order_by(LearningOutcome.sequence)
            .limit(1)
        )
        for subtopic in subtopics
    }
    for item_seq in range(1, TOPIC_QUIZ_ITEM_COUNT + 1):
        subtopic = subtopics[(item_seq - 1) % len(subtopics)]
        difficulty = _DIFFICULTIES[(item_seq - 1) % len(_DIFFICULTIES)]
        question = Question(subtopic_id=subtopic.id, code=f"{topic.slug}-{item_seq}")
        session.add(question)
        session.flush()
        question_version = QuestionVersion(
            question_id=question.id,
            version_number=1,
            prompt=f"{topic.name}: {subtopic.name} — question {item_seq}",
            question_type=QuestionType.MULTIPLE_CHOICE,
            difficulty=difficulty,
            item_kind=(
                QuestionItemKind.PROBLEM
                if difficulty is not QuestionDifficulty.EASY
                else QuestionItemKind.THEORY
            ),
            marks=Decimal("1.00"),
            lifecycle_status=QuestionVersionStatus.PUBLISHED,
        )
        session.add(question_version)
        session.flush()
        for label_index, label in enumerate("ABCD"):
            session.add(
                QuestionOption(
                    question_version_id=question_version.id,
                    label=label,
                    text=f"Option {label}",
                    sequence=label_index,
                )
            )
        session.add(
            QuestionAnswerKey(
                question_version_id=question_version.id,
                correct_option_label="A",
                correct_rationale=f"Option A matches {subtopic.name.lower()}.",
            )
        )
        outcome = outcomes.get(subtopic.id)
        if outcome is not None:
            session.add(
                QuestionOutcomeTag(
                    question_version_id=question_version.id,
                    learning_outcome_id=outcome.id,
                )
            )
        session.add(
            QuizItem(
                quiz_version_id=version.id,
                question_version_id=question_version.id,
                sequence=item_seq,
            )
        )


def retag_questions(
    session: Session,
    question_ids: list[UUID],
    subtopics: list[Subtopic],
) -> None:
    """Point existing questions at diagnostic subtopics and tag their outcome."""
    if not question_ids or not subtopics:
        return
    questions = list(session.scalars(select(Question).where(Question.id.in_(question_ids))))
    outcomes = []
    for subtopic in subtopics:
        outcome = session.scalar(
            select(LearningOutcome)
            .where(LearningOutcome.subtopic_id == subtopic.id)
            .order_by(LearningOutcome.sequence)
            .limit(1)
        )
        outcomes.append(outcome)
    for index, question in enumerate(questions):
        slot = index % len(subtopics)
        question.subtopic_id = subtopics[slot].id
        outcome = outcomes[slot]
        if outcome is None:
            continue
        versions = session.scalars(
            select(QuestionVersion).where(QuestionVersion.question_id == question.id)
        )
        for question_version in versions:
            existing = session.scalar(
                select(QuestionOutcomeTag.id).where(
                    QuestionOutcomeTag.question_version_id == question_version.id,
                    QuestionOutcomeTag.learning_outcome_id == outcome.id,
                )
            )
            if existing is None:
                session.add(
                    QuestionOutcomeTag(
                        question_version_id=question_version.id,
                        learning_outcome_id=outcome.id,
                    )
                )
