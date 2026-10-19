"""Pure draws: size, difficulty balance, rotation, and seeds. No database."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from education_platform.modules.assessments.models import QuestionDifficulty
from education_platform.modules.assessments.question_selection import (
    BankQuestion,
    ServeRecord,
    select_attempt_questions,
)

EASY = QuestionDifficulty.EASY
MEDIUM = QuestionDifficulty.MEDIUM
HARD = QuestionDifficulty.HARD


def _question(
    *,
    difficulty: QuestionDifficulty | None,
    subtopic_id: UUID | None = None,
    sequence: int,
    question_id: UUID | None = None,
) -> BankQuestion:
    return BankQuestion(
        question_version_id=uuid4(),
        question_id=question_id or uuid4(),
        difficulty=difficulty,
        subtopic_id=subtopic_id or uuid4(),
        bank_sequence=sequence,
    )


def _bank(
    counts: dict[QuestionDifficulty | None, int],
    *,
    subtopics: list[UUID] | None = None,
) -> list[BankQuestion]:
    subtopics = subtopics or [uuid4()]
    questions: list[BankQuestion] = []
    sequence = 1
    for difficulty, count in counts.items():
        for index in range(count):
            questions.append(
                _question(
                    difficulty=difficulty,
                    subtopic_id=subtopics[index % len(subtopics)],
                    sequence=sequence,
                )
            )
            sequence += 1
    return questions


def test_draw_of_ten_is_balanced_and_ordered_easy_to_hard() -> None:
    selected = select_attempt_questions(
        _bank({EASY: 12, MEDIUM: 12, HARD: 12}),
        draw_size=10,
        seed=1,
    )
    assert [question.number for question in selected] == list(range(1, 11))
    assert [question.difficulty for question in selected] == [EASY] * 4 + [MEDIUM] * 3 + [HARD] * 3


def test_small_bank_keeps_bank_order_and_sequence_numbers() -> None:
    bank = _bank({EASY: 2, HARD: 1, MEDIUM: 2})
    for draw_size in (None, 10):
        selected = select_attempt_questions(bank, draw_size=draw_size, seed=1)
        assert [question.number for question in selected] == [
            question.bank_sequence for question in bank
        ]
        assert [question.question_version_id for question in selected] == [
            question.question_version_id for question in bank
        ]


def test_short_levels_are_filled_from_neighbours() -> None:
    selected = select_attempt_questions(
        _bank({EASY: 10, MEDIUM: 1, HARD: 0}),
        draw_size=10,
        seed=1,
    )
    assert len(selected) == 10
    difficulties = [question.difficulty for question in selected]
    assert difficulties.count(EASY) == 9
    assert difficulties.count(MEDIUM) == 1
    assert difficulties == [EASY] * 9 + [MEDIUM]


def test_draw_spreads_across_subtopics() -> None:
    subtopics = [uuid4() for _ in range(4)]
    selected = select_attempt_questions(
        _bank({EASY: 20, MEDIUM: 20, HARD: 20}, subtopics=subtopics),
        draw_size=10,
        seed=3,
    )
    assert {question.subtopic_id for question in selected} == set(subtopics)


def test_retake_uses_unseen_questions() -> None:
    bank = _bank({EASY: 12, MEDIUM: 12, HARD: 12})
    served_at = datetime(2026, 1, 1, tzinfo=UTC)
    first = select_attempt_questions(bank, draw_size=10, seed=1)
    history = [
        ServeRecord(
            question_id=question.question_id,
            served_at=served_at,
            incorrect=False,
        )
        for question in first
    ]
    second = select_attempt_questions(bank, draw_size=10, history=history, seed=1)
    first_ids = {question.question_id for question in first}
    second_ids = {question.question_id for question in second}
    assert first_ids.isdisjoint(second_ids)
    assert [question.difficulty for question in second] == [EASY] * 4 + [MEDIUM] * 3 + [HARD] * 3


def test_short_hard_bucket_borrows_unseen_neighbours_before_recycling() -> None:
    """A topic bank of 8/8/4 leaves one unseen hard item after the first draw of 10.

    The second draw must take that item plus unseen medium questions, not two
    hard questions the student has already seen.
    """
    bank = _bank({EASY: 8, MEDIUM: 8, HARD: 4})
    served_at = datetime(2026, 1, 1, tzinfo=UTC)
    first = select_attempt_questions(bank, draw_size=10, seed=1)
    history = [
        ServeRecord(question_id=question.question_id, served_at=served_at, incorrect=False)
        for question in first
    ]
    second = select_attempt_questions(bank, draw_size=10, history=history, seed=1)
    first_ids = {question.question_id for question in first}
    assert first_ids.isdisjoint(question.question_id for question in second)
    assert [question.difficulty for question in second] == [EASY] * 4 + [MEDIUM] * 5 + [HARD]


def test_recycle_prefers_older_serves_then_wrong_answers() -> None:
    subtopic_id = uuid4()
    older = _question(difficulty=EASY, subtopic_id=subtopic_id, sequence=1)
    recent_wrong = _question(difficulty=EASY, subtopic_id=subtopic_id, sequence=2)
    recent_right = _question(difficulty=EASY, subtopic_id=subtopic_id, sequence=3)
    old = datetime(2026, 1, 1, tzinfo=UTC)
    recent = datetime(2026, 2, 1, tzinfo=UTC)
    selected = select_attempt_questions(
        [older, recent_wrong, recent_right],
        draw_size=1,
        history=[
            ServeRecord(question_id=older.question_id, served_at=old, incorrect=False),
            ServeRecord(question_id=recent_wrong.question_id, served_at=recent, incorrect=True),
            ServeRecord(question_id=recent_right.question_id, served_at=recent, incorrect=False),
        ],
        seed=1,
    )
    assert selected[0].question_id == older.question_id

    same_time = datetime(2026, 3, 1, tzinfo=UTC)
    unseen = _question(difficulty=EASY, subtopic_id=subtopic_id, sequence=4)
    recycled = select_attempt_questions(
        [older, recent_wrong, recent_right, unseen],
        draw_size=2,
        history=[
            ServeRecord(question_id=older.question_id, served_at=same_time, incorrect=False),
            ServeRecord(question_id=recent_wrong.question_id, served_at=same_time, incorrect=True),
            ServeRecord(question_id=recent_right.question_id, served_at=same_time, incorrect=False),
        ],
        seed=1,
    )
    assert {question.question_id for question in recycled} == {
        unseen.question_id,
        recent_wrong.question_id,
    }


def test_same_seed_is_stable_and_another_seed_can_differ() -> None:
    bank = _bank({EASY: 20})
    first = select_attempt_questions(bank, draw_size=10, seed=1)
    again = select_attempt_questions(bank, draw_size=10, seed=1)
    other = select_attempt_questions(bank, draw_size=10, seed=2)
    assert [question.question_version_id for question in first] == [
        question.question_version_id for question in again
    ]
    assert {question.question_version_id for question in first} != {
        question.question_version_id for question in other
    }


def test_history_matches_question_id_across_versions() -> None:
    question_id = uuid4()
    subtopic_id = uuid4()
    served_version = _question(
        difficulty=EASY, subtopic_id=subtopic_id, sequence=1, question_id=question_id
    )
    new_version = _question(
        difficulty=EASY, subtopic_id=subtopic_id, sequence=1, question_id=question_id
    )
    fresh = _question(difficulty=EASY, subtopic_id=subtopic_id, sequence=2)
    selected = select_attempt_questions(
        [new_version, fresh],
        draw_size=1,
        history=[
            ServeRecord(
                question_id=served_version.question_id,
                served_at=datetime(2026, 1, 1, tzinfo=UTC),
                incorrect=False,
            )
        ],
        seed=1,
    )
    assert selected[0].question_id == fresh.question_id
