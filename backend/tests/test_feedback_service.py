"""Unit tests for feedback_service's pure functions — no DB, hand-built row fixtures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from education_platform.modules.assessments import feedback_service
from education_platform.modules.assessments.feedback_service import (
    AnswerOutcomeRow,
    _classify_weak,
    _passing_streak,
    _review_interval_days,
    aggregate_by_subtopic,
    compute_trend,
    detect_regressions,
    find_repeated_distractor_pattern,
    learning_trajectory,
    subtopics_due_for_review,
)
from education_platform.modules.assessments.models import QuestionDifficulty
from education_platform.modules.assessments.schemas import SubtopicAttemptScore

SUBTOPIC_A = uuid4()
SUBTOPIC_B = uuid4()
ATTEMPT_1 = uuid4()
ATTEMPT_2 = uuid4()
ATTEMPT_3 = uuid4()
Q1 = uuid4()
Q2 = uuid4()
Q3 = uuid4()

T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = T1 + timedelta(days=7)
T3 = T1 + timedelta(days=14)


def _row(
    *,
    attempt_id: UUID = ATTEMPT_1,
    subtopic_id: UUID = SUBTOPIC_A,
    subtopic_name: str = "Subtopic A",
    topic_name: str = "Topic",
    marks_awarded: str = "1",
    marks_available: str = "1",
    question_version_id: UUID | None = None,
    selected_option_label: str | None = "A",
    is_correct: bool | None = True,
    difficulty: QuestionDifficulty | None = None,
    submitted_at: datetime | None = T1,
) -> AnswerOutcomeRow:
    return AnswerOutcomeRow(
        attempt_id=attempt_id,
        subtopic_id=subtopic_id,
        subtopic_name=subtopic_name,
        topic_name=topic_name,
        marks_awarded=Decimal(marks_awarded),
        marks_available=Decimal(marks_available),
        question_version_id=question_version_id or uuid4(),
        selected_option_label=selected_option_label,
        is_correct=is_correct,
        difficulty=difficulty,
        submitted_at=submitted_at,
    )


# ---------------------------------------------------------------------------
# aggregate_by_subtopic
# ---------------------------------------------------------------------------


def test_aggregate_all_correct() -> None:
    rows = [_row(is_correct=True), _row(is_correct=True, question_version_id=Q2)]
    result = aggregate_by_subtopic(rows)
    perf = result[SUBTOPIC_A]
    assert perf.percent == Decimal("100.00")
    assert perf.question_count == 2


def test_aggregate_all_wrong() -> None:
    rows = [
        _row(is_correct=False, marks_awarded="0"),
        _row(is_correct=False, marks_awarded="0", question_version_id=Q2),
    ]
    result = aggregate_by_subtopic(rows)
    assert result[SUBTOPIC_A].percent == Decimal("0.00")


def test_aggregate_easy_medium_hard_split() -> None:
    rows = [
        _row(question_version_id=Q1, difficulty=QuestionDifficulty.EASY, is_correct=True),
        _row(
            question_version_id=Q2,
            difficulty=QuestionDifficulty.EASY,
            is_correct=False,
            marks_awarded="0",
        ),
        _row(question_version_id=Q3, difficulty=QuestionDifficulty.MEDIUM, is_correct=True),
        _row(
            question_version_id=uuid4(),
            difficulty=QuestionDifficulty.HARD,
            is_correct=False,
            marks_awarded="0",
        ),
    ]
    perf = aggregate_by_subtopic(rows)[SUBTOPIC_A]
    assert perf.percent_easy == Decimal("50.00")
    assert perf.easy_question_count == 2
    assert perf.percent_medium == Decimal("100.00")
    assert perf.medium_question_count == 1
    assert perf.percent_hard == Decimal("0.00")
    assert perf.hard_question_count == 1


def test_aggregate_missing_difficulty_tier_is_none_not_zero() -> None:
    """No hard questions answered -> percent_hard is None (no evidence), not 0%."""
    rows = [_row(difficulty=QuestionDifficulty.EASY, is_correct=True)]
    perf = aggregate_by_subtopic(rows)[SUBTOPIC_A]
    assert perf.percent_hard is None
    assert perf.hard_question_count == 0


def test_aggregate_multiple_subtopics_stay_separate() -> None:
    rows = [
        _row(subtopic_id=SUBTOPIC_A, is_correct=True),
        _row(subtopic_id=SUBTOPIC_B, is_correct=False, marks_awarded="0", question_version_id=Q2),
    ]
    result = aggregate_by_subtopic(rows)
    assert result[SUBTOPIC_A].percent == Decimal("100.00")
    assert result[SUBTOPIC_B].percent == Decimal("0.00")


# ---------------------------------------------------------------------------
# _classify_weak
# ---------------------------------------------------------------------------


def test_classify_weak_below_absolute_threshold_no_classmates() -> None:
    is_weak = _classify_weak(
        Decimal("50"),
        10,
        [],
        threshold_percent=Decimal("70"),
        relative_sd=1.0,
        min_class_n=5,
        min_student_n=3,
    )
    assert is_weak is True


def test_classify_weak_at_or_above_threshold_no_classmates() -> None:
    is_weak = _classify_weak(
        Decimal("70"),
        10,
        [],
        threshold_percent=Decimal("70"),
        relative_sd=1.0,
        min_class_n=5,
        min_student_n=3,
    )
    assert is_weak is False


def test_classify_weak_insufficient_student_evidence_returns_none() -> None:
    """Below min_student_n questions answered -> no verdict, not a false flag either way."""
    is_weak = _classify_weak(
        Decimal("0"),
        2,
        [],
        threshold_percent=Decimal("70"),
        relative_sd=1.0,
        min_class_n=5,
        min_student_n=3,
    )
    assert is_weak is None


def test_classify_weak_class_too_small_ignores_adaptive_floor() -> None:
    """Fewer classmates than min_class_n -> only the absolute bar applies."""
    classmates = [Decimal("10"), Decimal("10")]  # n=2, below min_class_n=5
    is_weak = _classify_weak(
        Decimal("60"),
        10,
        classmates,
        threshold_percent=Decimal("70"),
        relative_sd=1.0,
        min_class_n=5,
        min_student_n=3,
    )
    # 60 < 70 (absolute bar) -> weak, regardless of the tiny/low classmate sample
    assert is_weak is True


def test_classify_weak_hard_test_lowers_bar_via_adaptive_floor() -> None:
    """Class did poorly -> adaptive floor drops below the absolute bar, so a student who
    is below the raw pass mark but in line with a hard test's class performance is NOT
    flagged — only genuinely-behind-peers students are."""
    classmates = [Decimal("45"), Decimal("50"), Decimal("55"), Decimal("50"), Decimal("50")]
    # mean=50, stddev=~3.16, adaptive_bar = 50 - 1*3.16 ~= 46.8 -> bar = min(70, 46.8) = 46.8
    not_weak = _classify_weak(
        Decimal("50"),
        10,
        classmates,
        threshold_percent=Decimal("70"),
        relative_sd=1.0,
        min_class_n=5,
        min_student_n=3,
    )
    assert not_weak is False

    genuinely_behind = _classify_weak(
        Decimal("30"),
        10,
        classmates,
        threshold_percent=Decimal("70"),
        relative_sd=1.0,
        min_class_n=5,
        min_student_n=3,
    )
    assert genuinely_behind is True


# ---------------------------------------------------------------------------
# compute_trend
# ---------------------------------------------------------------------------


def test_trend_single_attempt_is_none_not_zero() -> None:
    rows = [_row(attempt_id=ATTEMPT_1, submitted_at=T1)]
    prior, delta = compute_trend(rows)[SUBTOPIC_A]
    assert prior is None
    assert delta is None


def test_trend_two_attempts_improving() -> None:
    rows = [
        _row(attempt_id=ATTEMPT_1, submitted_at=T1, is_correct=False, marks_awarded="0"),
        _row(attempt_id=ATTEMPT_2, submitted_at=T2, is_correct=True, question_version_id=Q2),
    ]
    prior, delta = compute_trend(rows)[SUBTOPIC_A]
    assert prior == Decimal("0.00")
    assert delta == Decimal("100.00")


def test_trend_uses_only_the_last_two_of_three_attempts() -> None:
    rows = [
        _row(attempt_id=ATTEMPT_1, submitted_at=T1, is_correct=False, marks_awarded="0"),
        _row(
            attempt_id=ATTEMPT_2,
            submitted_at=T2,
            is_correct=True,
            question_version_id=Q2,
        ),
        _row(
            attempt_id=ATTEMPT_3,
            submitted_at=T3,
            is_correct=False,
            marks_awarded="0",
            question_version_id=Q3,
        ),
    ]
    prior, delta = compute_trend(rows)[SUBTOPIC_A]
    # prior = attempt 2 (100%), latest = attempt 3 (0%) -- attempt 1 is out of scope
    assert prior == Decimal("100.00")
    assert delta == Decimal("-100.00")


def test_trend_missing_submitted_at_does_not_crash() -> None:
    rows = [
        _row(attempt_id=ATTEMPT_1, submitted_at=None),
        _row(attempt_id=ATTEMPT_2, submitted_at=None, question_version_id=Q2),
    ]
    prior, delta = compute_trend(rows)[SUBTOPIC_A]
    assert prior is not None
    assert delta is not None


# ---------------------------------------------------------------------------
# detect_regressions
# ---------------------------------------------------------------------------


def test_regression_correct_then_wrong_is_counted() -> None:
    rows = [
        _row(attempt_id=ATTEMPT_1, submitted_at=T1, question_version_id=Q1, is_correct=True),
        _row(attempt_id=ATTEMPT_2, submitted_at=T2, question_version_id=Q1, is_correct=False),
    ]
    assert detect_regressions(rows)[SUBTOPIC_A] == 1


def test_regression_wrong_then_wrong_not_counted() -> None:
    rows = [
        _row(attempt_id=ATTEMPT_1, submitted_at=T1, question_version_id=Q1, is_correct=False),
        _row(attempt_id=ATTEMPT_2, submitted_at=T2, question_version_id=Q1, is_correct=False),
    ]
    assert SUBTOPIC_A not in detect_regressions(rows)


def test_regression_wrong_then_correct_is_improvement_not_regression() -> None:
    rows = [
        _row(attempt_id=ATTEMPT_1, submitted_at=T1, question_version_id=Q1, is_correct=False),
        _row(attempt_id=ATTEMPT_2, submitted_at=T2, question_version_id=Q1, is_correct=True),
    ]
    assert SUBTOPIC_A not in detect_regressions(rows)


def test_regression_correct_then_correct_not_counted() -> None:
    rows = [
        _row(attempt_id=ATTEMPT_1, submitted_at=T1, question_version_id=Q1, is_correct=True),
        _row(attempt_id=ATTEMPT_2, submitted_at=T2, question_version_id=Q1, is_correct=True),
    ]
    assert SUBTOPIC_A not in detect_regressions(rows)


def test_regression_single_attempt_no_entry() -> None:
    rows = [_row(attempt_id=ATTEMPT_1, submitted_at=T1, question_version_id=Q1)]
    assert SUBTOPIC_A not in detect_regressions(rows)


def test_regression_counts_multiple_questions_and_scopes_by_subtopic() -> None:
    rows = [
        # subtopic A: two questions regress
        _row(
            attempt_id=ATTEMPT_1,
            subtopic_id=SUBTOPIC_A,
            submitted_at=T1,
            question_version_id=Q1,
            is_correct=True,
        ),
        _row(
            attempt_id=ATTEMPT_1,
            subtopic_id=SUBTOPIC_A,
            submitted_at=T1,
            question_version_id=Q2,
            is_correct=True,
        ),
        _row(
            attempt_id=ATTEMPT_2,
            subtopic_id=SUBTOPIC_A,
            submitted_at=T2,
            question_version_id=Q1,
            is_correct=False,
        ),
        _row(
            attempt_id=ATTEMPT_2,
            subtopic_id=SUBTOPIC_A,
            submitted_at=T2,
            question_version_id=Q2,
            is_correct=False,
        ),
        # subtopic B: no regression (stays correct)
        _row(
            attempt_id=ATTEMPT_1,
            subtopic_id=SUBTOPIC_B,
            submitted_at=T1,
            question_version_id=Q3,
            is_correct=True,
        ),
        _row(
            attempt_id=ATTEMPT_2,
            subtopic_id=SUBTOPIC_B,
            submitted_at=T2,
            question_version_id=Q3,
            is_correct=True,
        ),
    ]
    result = detect_regressions(rows)
    assert result[SUBTOPIC_A] == 2
    assert SUBTOPIC_B not in result


# ---------------------------------------------------------------------------
# find_repeated_distractor_pattern
# ---------------------------------------------------------------------------


def test_distractor_pattern_found_at_min_repeats() -> None:
    rows = [
        _row(question_version_id=Q1, is_correct=False, selected_option_label="C"),
        _row(question_version_id=Q2, is_correct=False, selected_option_label="C"),
    ]
    assert find_repeated_distractor_pattern(rows, SUBTOPIC_A) == "C"


def test_distractor_pattern_below_min_repeats_is_none() -> None:
    rows = [_row(question_version_id=Q1, is_correct=False, selected_option_label="C")]
    assert find_repeated_distractor_pattern(rows, SUBTOPIC_A) is None


def test_distractor_pattern_picks_most_common_label() -> None:
    rows = [
        _row(question_version_id=Q1, is_correct=False, selected_option_label="C"),
        _row(question_version_id=Q2, is_correct=False, selected_option_label="C"),
        _row(question_version_id=Q3, is_correct=False, selected_option_label="D"),
    ]
    assert find_repeated_distractor_pattern(rows, SUBTOPIC_A) == "C"


def test_distractor_pattern_no_wrong_answers_is_none() -> None:
    rows = [_row(question_version_id=Q1, is_correct=True, selected_option_label="C")]
    assert find_repeated_distractor_pattern(rows, SUBTOPIC_A) is None


def test_distractor_pattern_correct_answers_dont_count_toward_repeats() -> None:
    """Picking 'C' correctly twice, then wrong once, shouldn't read as a 2x wrong pattern."""
    rows = [
        _row(question_version_id=Q1, is_correct=True, selected_option_label="C"),
        _row(question_version_id=Q2, is_correct=True, selected_option_label="C"),
        _row(question_version_id=Q3, is_correct=False, selected_option_label="C"),
    ]
    assert find_repeated_distractor_pattern(rows, SUBTOPIC_A) is None


def test_distractor_pattern_scoped_to_requested_subtopic() -> None:
    rows = [
        _row(
            subtopic_id=SUBTOPIC_A,
            question_version_id=Q1,
            is_correct=False,
            selected_option_label="C",
        ),
        _row(
            subtopic_id=SUBTOPIC_B,
            question_version_id=Q2,
            is_correct=False,
            selected_option_label="C",
        ),
    ]
    assert find_repeated_distractor_pattern(rows, SUBTOPIC_A) is None
    assert find_repeated_distractor_pattern(rows, SUBTOPIC_B) is None


@pytest.mark.parametrize("min_repeats", [1, 3])
def test_distractor_pattern_respects_custom_min_repeats(min_repeats: int) -> None:
    rows = [
        _row(question_version_id=Q1, is_correct=False, selected_option_label="C"),
        _row(question_version_id=Q2, is_correct=False, selected_option_label="C"),
    ]
    result = find_repeated_distractor_pattern(rows, SUBTOPIC_A, min_repeats=min_repeats)
    assert (result == "C") == (min_repeats <= 2)


# ---------------------------------------------------------------------------
# review-interval logic (_passing_streak, _review_interval_days, subtopics_due_for_review)
# ---------------------------------------------------------------------------


def _score(sequence: int, percent: str, submitted_at: datetime = T1) -> SubtopicAttemptScore:
    return SubtopicAttemptScore(
        sequence=sequence, percent=Decimal(percent), submitted_at=submitted_at
    )


def test_passing_streak_counts_back_from_latest() -> None:
    attempts = [_score(1, "60"), _score(2, "80"), _score(3, "90")]
    assert _passing_streak(attempts, Decimal("70")) == 2


def test_passing_streak_breaks_at_first_failure_from_the_end() -> None:
    attempts = [_score(1, "90"), _score(2, "60"), _score(3, "90")]
    assert _passing_streak(attempts, Decimal("70")) == 1


def test_passing_streak_zero_when_latest_fails() -> None:
    attempts = [_score(1, "90"), _score(2, "40")]
    assert _passing_streak(attempts, Decimal("70")) == 0


@pytest.mark.parametrize(
    ("streak", "expected_days"),
    [(1, 3), (2, 10), (3, 21), (4, 21), (10, 21)],
)
def test_review_interval_schedule_grows_with_streak(streak: int, expected_days: int) -> None:
    """More consolidation (a longer passing streak) -> a longer interval, capped at the last
    tier once a streak exceeds this platform's realistic max (quizzes allow 3 attempts)."""
    assert _review_interval_days(streak) == expected_days


async def _no_remediation(_session: object, _subtopic_id: UUID) -> None:
    return None


async def test_due_for_review_just_passed_is_not_due(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback_service, "_remediation_for_subtopic", _no_remediation)
    rows = [_row(submitted_at=T1, is_correct=True)]
    result = await subtopics_due_for_review(None, rows, pass_mark=Decimal("70"), now=T1)
    assert result == []


async def test_due_for_review_past_interval_is_due(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback_service, "_remediation_for_subtopic", _no_remediation)
    rows = [_row(submitted_at=T1, is_correct=True)]
    now = T1 + timedelta(days=10)
    result = await subtopics_due_for_review(None, rows, pass_mark=Decimal("70"), now=now)
    assert len(result) == 1
    assert result[0].subtopic_id == SUBTOPIC_A
    assert result[0].due_interval_days == 3
    assert result[0].days_since_last_attempt == 10


async def test_due_for_review_more_consolidation_longer_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three consecutive passes -> a 21-day interval, not the first-pass 3-day one."""
    monkeypatch.setattr(feedback_service, "_remediation_for_subtopic", _no_remediation)
    rows = [
        _row(attempt_id=ATTEMPT_1, submitted_at=T1, is_correct=True, question_version_id=Q1),
        _row(attempt_id=ATTEMPT_2, submitted_at=T2, is_correct=True, question_version_id=Q1),
        _row(attempt_id=ATTEMPT_3, submitted_at=T3, is_correct=True, question_version_id=Q1),
    ]
    now = T3 + timedelta(days=25)
    result = await subtopics_due_for_review(None, rows, pass_mark=Decimal("70"), now=now)
    assert len(result) == 1
    assert result[0].due_interval_days == 21


async def test_due_for_review_currently_failing_is_never_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feedback_service, "_remediation_for_subtopic", _no_remediation)
    rows = [_row(submitted_at=T1, is_correct=False, marks_awarded="0")]
    now = T1 + timedelta(days=90)
    result = await subtopics_due_for_review(None, rows, pass_mark=Decimal("70"), now=now)
    assert result == []


# ---------------------------------------------------------------------------
# learning_trajectory
# ---------------------------------------------------------------------------


def test_trajectory_needs_at_least_two_points() -> None:
    assert learning_trajectory([_score(1, "40")]) is None


def test_trajectory_two_points_is_enough_with_low_evidence() -> None:
    """A trend is visible after the 2nd attempt, not only once a 3-attempt budget is spent."""
    history = [_score(1, "40"), _score(2, "70")]
    result = learning_trajectory(history)
    assert result is not None
    trend, recent_change, evidence_level = result
    assert trend == "improving"
    assert recent_change == Decimal("30")
    assert evidence_level == "low"


def test_trajectory_medium_evidence_at_three_points() -> None:
    """3 attempts is most/all of this platform's realistic budget -> medium evidence."""
    history = [_score(1, "40"), _score(2, "55"), _score(3, "70")]
    result = learning_trajectory(history)
    assert result is not None
    trend, _recent_change, evidence_level = result
    assert trend == "improving"
    assert evidence_level == "medium"


def test_trajectory_flat_within_band() -> None:
    history = [_score(1, "60"), _score(2, "62"), _score(3, "63")]
    result = learning_trajectory(history)
    assert result is not None
    assert result[0] == "flat"


def test_trajectory_flat_band_widened_past_a_single_question_swing() -> None:
    """A +7 change would have been 'improving' under the old +-5 band; the widened +-10 band
    now reads it as 'flat' -- one question's worth of noise on a typical 10-question quiz
    shouldn't be called a real trend."""
    history = [_score(1, "60"), _score(2, "67")]
    result = learning_trajectory(history)
    assert result is not None
    assert result[0] == "flat"


def test_trajectory_declining() -> None:
    history = [_score(1, "80"), _score(2, "65"), _score(3, "60")]
    result = learning_trajectory(history)
    assert result is not None
    trend, recent_change, _evidence_level = result
    assert trend == "declining"
    assert recent_change == Decimal("-5")
