"""Choose the question versions one quiz attempt serves.

Start-attempt calls `assign_attempt_questions` and then reads the stored rows
back. Submit, result, and feedback grade those same rows. The draw itself is a
pure function so tests can pin balance, rotation, and seeds without Postgres.

`QuestionDifficulty` is easy / medium / hard. A draw of 10 therefore aims for
4 easy, 3 medium, and 3 hard (the remainder lands on the easier levels). There
is no "very hard" tag to reserve a slot for.
"""

from __future__ import annotations

import random
import secrets
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.assessments.models import (
    AttemptAnswer,
    AttemptQuestion,
    Question,
    QuestionDifficulty,
    QuestionVersion,
    QuizAttempt,
    QuizItem,
    QuizVersion,
)
from education_platform.modules.assessments.queries import questions_for_numbered
from education_platform.modules.assessments.schemas import QuizQuestion

# Generated topic-mastery quizzes store this on the quiz version. NULL on the
# version still means "serve the whole bank".
DEFAULT_ATTEMPT_QUESTION_COUNT = 10

_LEVELS: tuple[QuestionDifficulty, ...] = (
    QuestionDifficulty.EASY,
    QuestionDifficulty.MEDIUM,
    QuestionDifficulty.HARD,
)
_NEIGHBORS: dict[QuestionDifficulty, tuple[QuestionDifficulty, ...]] = {
    QuestionDifficulty.EASY: (QuestionDifficulty.MEDIUM, QuestionDifficulty.HARD),
    QuestionDifficulty.MEDIUM: (QuestionDifficulty.EASY, QuestionDifficulty.HARD),
    QuestionDifficulty.HARD: (QuestionDifficulty.MEDIUM, QuestionDifficulty.EASY),
}
_DIFFICULTY_RANK: dict[QuestionDifficulty, int] = {
    level: index for index, level in enumerate(_LEVELS)
}


@dataclass(frozen=True, slots=True)
class BankQuestion:
    question_version_id: UUID
    question_id: UUID
    difficulty: QuestionDifficulty | None
    subtopic_id: UUID
    bank_sequence: int


@dataclass(frozen=True, slots=True)
class ServeRecord:
    """Latest time this student was served `question_id` on this quiz."""

    question_id: UUID
    served_at: datetime
    incorrect: bool


@dataclass(frozen=True, slots=True)
class SelectedQuestion:
    number: int
    question_version_id: UUID
    question_id: UUID
    difficulty: QuestionDifficulty | None
    subtopic_id: UUID
    bank_sequence: int


@dataclass(frozen=True, slots=True)
class ServedQuestion:
    number: int
    question_version_id: UUID


def select_attempt_questions(
    bank: list[BankQuestion] | tuple[BankQuestion, ...],
    *,
    draw_size: int | None,
    history: list[ServeRecord] | tuple[ServeRecord, ...] = (),
    seed: int | None = None,
) -> list[SelectedQuestion]:
    """Questions to serve, already numbered.

    `draw_size is None`, or a bank no larger than `draw_size`, returns every
    item in bank order with its original sequence number. A real draw is
    balanced across difficulty, spread across subtopics, and ordered
    easy → hard with numbers 1..n.
    """
    ordered = sorted(
        bank, key=lambda question: (question.bank_sequence, str(question.question_version_id))
    )
    if draw_size is None or len(ordered) <= draw_size:
        return [_selected(question, question.bank_sequence) for question in ordered]

    rng = random.Random(seed if seed is not None else secrets.randbits(64))
    chosen = _draw(ordered, draw_size, _latest_history(history), rng)
    chosen.sort(
        key=lambda question: (
            _difficulty_rank(question.difficulty),
            question.bank_sequence,
            str(question.question_version_id),
        )
    )
    return [_selected(question, number) for number, question in enumerate(chosen, start=1)]


def _selected(question: BankQuestion, number: int) -> SelectedQuestion:
    return SelectedQuestion(
        number=number,
        question_version_id=question.question_version_id,
        question_id=question.question_id,
        difficulty=question.difficulty,
        subtopic_id=question.subtopic_id,
        bank_sequence=question.bank_sequence,
    )


def _difficulty_rank(difficulty: QuestionDifficulty | None) -> int:
    if difficulty is None:
        return len(_LEVELS)
    return _DIFFICULTY_RANK.get(difficulty, len(_LEVELS))


def _targets(draw_size: int) -> dict[QuestionDifficulty, int]:
    """Split `draw_size` across easy, medium, hard. Remainder goes to easier levels."""
    base, extra = divmod(draw_size, len(_LEVELS))
    return {level: base + (1 if index < extra else 0) for index, level in enumerate(_LEVELS)}


def _latest_history(
    history: list[ServeRecord] | tuple[ServeRecord, ...],
) -> dict[UUID, ServeRecord]:
    latest: dict[UUID, ServeRecord] = {}
    for record in history:
        current = latest.get(record.question_id)
        newer = current is None or record.served_at > current.served_at
        same_time_wrong = (
            current is not None
            and record.served_at == current.served_at
            and record.incorrect
            and not current.incorrect
        )
        if newer or same_time_wrong:
            latest[record.question_id] = record
    return latest


def _tier(question: BankQuestion, history: dict[UUID, ServeRecord]) -> tuple[int, datetime, int]:
    """Unseen first. Then oldest serve. Same timestamp: wrong answers before right ones."""
    record = history.get(question.question_id)
    if record is None:
        return (0, datetime.min.replace(tzinfo=UTC), 0)
    return (1, record.served_at, 0 if record.incorrect else 1)


def _split_levels(
    questions: list[BankQuestion],
) -> tuple[dict[QuestionDifficulty, list[BankQuestion]], list[BankQuestion]]:
    by_level: dict[QuestionDifficulty, list[BankQuestion]] = {level: [] for level in _LEVELS}
    untagged: list[BankQuestion] = []
    for question in questions:
        if question.difficulty is None:
            untagged.append(question)
            continue
        bucket = by_level.get(question.difficulty)
        if bucket is None:
            untagged.append(question)
        else:
            bucket.append(question)
    return by_level, untagged


def _draw(
    bank: list[BankQuestion],
    draw_size: int,
    history: dict[UUID, ServeRecord],
    rng: random.Random,
) -> list[BankQuestion]:
    """Fill difficulty targets from unseen questions, then recycle seen ones.

    A short level borrows from neighbouring levels before it reuses a question
    the student has already been served.
    """
    unseen = [question for question in bank if question.question_id not in history]
    seen = [question for question in bank if question.question_id in history]
    excluded: set[UUID] = set()
    chosen: list[BankQuestion] = []

    def take(pool: list[BankQuestion], count: int) -> list[BankQuestion]:
        picked = _pick(pool, count, history=history, rng=rng, excluded=excluded)
        excluded.update(question.question_version_id for question in picked)
        return picked

    def fill(pool: list[BankQuestion], shortfall: dict[QuestionDifficulty, int]) -> None:
        by_level, untagged = _split_levels(pool)
        for level in _LEVELS:
            picked = take(by_level[level], shortfall[level])
            chosen.extend(picked)
            shortfall[level] -= len(picked)
        for level in _LEVELS:
            deficit = shortfall[level]
            for neighbor in _NEIGHBORS[level]:
                if deficit <= 0:
                    break
                picked = take(by_level[neighbor], deficit)
                chosen.extend(picked)
                deficit -= len(picked)
            if deficit > 0:
                picked = take(untagged, deficit)
                chosen.extend(picked)
                deficit -= len(picked)
            shortfall[level] = deficit

    shortfall = _targets(draw_size)
    fill(unseen, shortfall)
    if len(chosen) < draw_size:
        fill(seen, shortfall)
    if len(chosen) < draw_size:
        chosen.extend(take(bank, draw_size - len(chosen)))
    return chosen[:draw_size]


def _pick(
    pool: list[BankQuestion],
    count: int,
    *,
    history: dict[UUID, ServeRecord],
    rng: random.Random,
    excluded: set[UUID],
) -> list[BankQuestion]:
    available = [question for question in pool if question.question_version_id not in excluded]
    if count <= 0 or not available:
        return []
    buckets: dict[tuple[int, datetime, int], list[BankQuestion]] = defaultdict(list)
    for question in available:
        buckets[_tier(question, history)].append(question)
    chosen: list[BankQuestion] = []
    for key in sorted(buckets):
        need = count - len(chosen)
        if need <= 0:
            break
        chosen.extend(_round_robin(buckets[key], need, rng))
    return chosen


def _round_robin(pool: list[BankQuestion], count: int, rng: random.Random) -> list[BankQuestion]:
    """One question per subtopic per pass. Ties inside a subtopic follow `rng`."""
    if count <= 0 or not pool:
        return []
    ties = {question.question_version_id: rng.random() for question in pool}
    groups: dict[UUID, list[BankQuestion]] = defaultdict(list)
    for question in pool:
        groups[question.subtopic_id].append(question)
    for group in groups.values():
        group.sort(
            key=lambda question: (ties[question.question_version_id], question.bank_sequence)
        )
    order = list(groups)
    rng.shuffle(order)
    chosen: list[BankQuestion] = []
    while len(chosen) < count:
        progressed = False
        for subtopic_id in order:
            group = groups[subtopic_id]
            if not group:
                continue
            chosen.append(group.pop(0))
            progressed = True
            if len(chosen) == count:
                break
        if not progressed:
            break
    return chosen


async def assign_attempt_questions(
    session: AsyncSession,
    *,
    attempt: QuizAttempt,
    quiz_id: UUID,
    quiz_version: QuizVersion,
    seed: int | None = None,
) -> None:
    """Persist the set this attempt will show. A later read returns the same rows."""
    bank = await _load_bank(session, quiz_version.id)
    history = await _load_serve_history(
        session,
        student_id=attempt.student_id,
        quiz_id=quiz_id,
        exclude_attempt_id=attempt.id,
    )
    selected = select_attempt_questions(
        bank,
        draw_size=quiz_version.attempt_question_count,
        history=history,
        seed=seed,
    )
    for question in selected:
        session.add(
            AttemptQuestion(
                attempt_id=attempt.id,
                question_version_id=question.question_version_id,
                position=question.number,
            )
        )
    await session.flush()


async def served_questions_for_attempt(
    session: AsyncSession, attempt_id: UUID, quiz_version_id: UUID
) -> list[ServedQuestion]:
    """Stored draw, or the full quiz-item bank when this attempt predates the table."""
    stored = (
        await session.scalars(
            select(AttemptQuestion)
            .where(AttemptQuestion.attempt_id == attempt_id)
            .order_by(AttemptQuestion.position)
        )
    ).all()
    if stored:
        return [
            ServedQuestion(number=row.position, question_version_id=row.question_version_id)
            for row in stored
        ]
    items = (
        await session.scalars(
            select(QuizItem)
            .where(QuizItem.quiz_version_id == quiz_version_id)
            .order_by(QuizItem.sequence)
        )
    ).all()
    return [
        ServedQuestion(number=item.sequence, question_version_id=item.question_version_id)
        for item in items
    ]


async def questions_for_attempt(session: AsyncSession, attempt: QuizAttempt) -> list[QuizQuestion]:
    served = await served_questions_for_attempt(session, attempt.id, attempt.quiz_version_id)
    return await questions_for_numbered(
        session,
        [(question.number, question.question_version_id) for question in served],
    )


async def _load_bank(session: AsyncSession, quiz_version_id: UUID) -> list[BankQuestion]:
    rows = (
        await session.execute(
            select(
                QuizItem.question_version_id,
                QuizItem.sequence,
                QuestionVersion.question_id,
                QuestionVersion.difficulty,
                Question.subtopic_id,
            )
            .join(QuestionVersion, QuestionVersion.id == QuizItem.question_version_id)
            .join(Question, Question.id == QuestionVersion.question_id)
            .where(QuizItem.quiz_version_id == quiz_version_id)
            .order_by(QuizItem.sequence)
        )
    ).all()
    return [
        BankQuestion(
            question_version_id=question_version_id,
            question_id=question_id,
            difficulty=difficulty,
            subtopic_id=subtopic_id,
            bank_sequence=sequence,
        )
        for question_version_id, sequence, question_id, difficulty, subtopic_id in rows
    ]


async def _load_serve_history(
    session: AsyncSession,
    *,
    student_id: UUID,
    quiz_id: UUID,
    exclude_attempt_id: UUID,
) -> list[ServeRecord]:
    """Prior serves of this quiz, matched on question identity across versions.

    Attempts with no `attempt_questions` rows count as having served the full
    bank (the historical behaviour).
    """
    attempts = (
        await session.execute(
            select(
                QuizAttempt.id,
                QuizAttempt.quiz_version_id,
                QuizAttempt.started_at,
                QuizAttempt.created_at,
            )
            .join(QuizVersion, QuizVersion.id == QuizAttempt.quiz_version_id)
            .where(
                QuizAttempt.student_id == student_id,
                QuizVersion.quiz_id == quiz_id,
                QuizAttempt.id != exclude_attempt_id,
            )
        )
    ).all()
    if not attempts:
        return []

    served_at_by_attempt = {
        attempt_id: started_at or created_at for attempt_id, _, started_at, created_at in attempts
    }
    version_by_attempt = {
        attempt_id: quiz_version_id for attempt_id, quiz_version_id, _, _ in attempts
    }
    attempt_ids = list(served_at_by_attempt)
    stored = (
        await session.execute(
            select(
                AttemptQuestion.attempt_id,
                QuestionVersion.question_id,
                AttemptAnswer.is_correct,
            )
            .join(QuestionVersion, QuestionVersion.id == AttemptQuestion.question_version_id)
            .outerjoin(
                AttemptAnswer,
                and_(
                    AttemptAnswer.attempt_id == AttemptQuestion.attempt_id,
                    AttemptAnswer.question_version_id == AttemptQuestion.question_version_id,
                ),
            )
            .where(AttemptQuestion.attempt_id.in_(attempt_ids))
        )
    ).all()
    events: list[tuple[UUID, datetime, bool]] = [
        (question_id, served_at_by_attempt[attempt_id], is_correct is False)
        for attempt_id, question_id, is_correct in stored
    ]
    attempts_with_rows = {attempt_id for attempt_id, _, _ in stored}
    legacy_ids = [attempt_id for attempt_id in attempt_ids if attempt_id not in attempts_with_rows]
    if legacy_ids:
        events.extend(
            await _legacy_serve_events(
                session,
                legacy_ids=legacy_ids,
                version_by_attempt=version_by_attempt,
                served_at_by_attempt=served_at_by_attempt,
            )
        )
    return list(_latest_history(_records(events)).values())


def _records(events: list[tuple[UUID, datetime, bool]]) -> list[ServeRecord]:
    return [
        ServeRecord(question_id=question_id, served_at=served_at, incorrect=incorrect)
        for question_id, served_at, incorrect in events
    ]


async def _legacy_serve_events(
    session: AsyncSession,
    *,
    legacy_ids: list[UUID],
    version_by_attempt: dict[UUID, UUID],
    served_at_by_attempt: dict[UUID, datetime],
) -> list[tuple[UUID, datetime, bool]]:
    versions = {version_by_attempt[attempt_id] for attempt_id in legacy_ids}
    item_rows = (
        await session.execute(
            select(QuizItem.quiz_version_id, QuestionVersion.question_id)
            .join(QuestionVersion, QuestionVersion.id == QuizItem.question_version_id)
            .where(QuizItem.quiz_version_id.in_(versions))
        )
    ).all()
    question_ids_by_version: dict[UUID, list[UUID]] = defaultdict(list)
    for quiz_version_id, question_id in item_rows:
        question_ids_by_version[quiz_version_id].append(question_id)
    answer_rows = (
        await session.execute(
            select(
                AttemptAnswer.attempt_id,
                QuestionVersion.question_id,
                AttemptAnswer.is_correct,
            )
            .join(QuestionVersion, QuestionVersion.id == AttemptAnswer.question_version_id)
            .where(AttemptAnswer.attempt_id.in_(legacy_ids))
        )
    ).all()
    incorrect = {
        (attempt_id, question_id): is_correct is False
        for attempt_id, question_id, is_correct in answer_rows
    }
    events: list[tuple[UUID, datetime, bool]] = []
    for attempt_id in legacy_ids:
        served_at = served_at_by_attempt[attempt_id]
        for question_id in question_ids_by_version.get(version_by_attempt[attempt_id], []):
            events.append((question_id, served_at, incorrect.get((attempt_id, question_id), False)))
    return events
