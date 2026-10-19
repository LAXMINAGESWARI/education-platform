"""Thresholds and which checks are allowed to change the gate."""

from __future__ import annotations

from education_platform.modules.judge.checks import (
    ItemTarget,
    LessonTarget,
    item_plan,
    lesson_plan,
    looks_computational,
)
from education_platform.modules.judge.policy import (
    DECISION_ACCEPT,
    DECISION_REJECT,
    DECISION_REVIEW,
    Thresholds,
    apply_policy,
)
from education_platform.modules.judge.types import ChoiceAnswer, NoulAnswer, ScoreAnswer

_LIMITS = Thresholds()


def test_lesson_plan_asks_for_grounding_coverage_grade_and_math() -> None:
    plan = lesson_plan(
        LessonTarget(
            heading="Area",
            grade="Grade 6",
            outcomes=("Find the area of a rectangle.",),
            chunks=("A rectangle's area is length times width.",),
            markdown=(
                "The area of a rectangle is the product of its length and its width. "
                "A 3 by 4 rectangle has area 12."
            ),
        ),
        include_claims=True,
    )
    ids = [check.check_id for check in plan]
    assert "grounded" in ids
    assert "coverage_01" in ids
    assert "grade_level" in ids
    assert "math_table" in ids
    assert any(check_id.startswith("claim_") for check_id in ids)


def test_noul_thresholds() -> None:
    plan = item_plan(
        ItemTarget(
            heading="Area",
            grade="Grade 6",
            outcomes=(),
            chunks=(),
            prompt="What is 3 times 4?",
            options={"A": "7", "B": "12", "C": "14", "D": "10"},
            correct_label="B",
            correct_rationale="3 times 4 is 12.",
            difficulty="easy",
        )
    )
    keyed = next(check for check in plan if check.check_id == "keyed_correct")
    high = apply_policy((keyed,), {"keyed_correct": NoulAnswer(0.91)}, thresholds=_LIMITS)
    mid = apply_policy((keyed,), {"keyed_correct": NoulAnswer(0.55)}, thresholds=_LIMITS)
    low = apply_policy((keyed,), {"keyed_correct": NoulAnswer(0.20)}, thresholds=_LIMITS)
    assert high.decision == DECISION_ACCEPT
    assert mid.decision == DECISION_REVIEW
    assert low.decision == DECISION_REJECT
    assert high.checks[0].probabilities["true"] == 0.91


def test_computational_key_still_rejects_the_item() -> None:
    assert looks_computational("What is 3 * 4?", "3 * 4 = 12", ["12"])
    plan = item_plan(
        ItemTarget(
            heading="Area",
            grade="Grade 6",
            outcomes=(),
            chunks=(),
            prompt="What is 3 * 4?",
            options={"A": "7", "B": "12", "C": "14", "D": "10"},
            correct_label="B",
            correct_rationale="3 * 4 = 12.",
            difficulty="easy",
        )
    )
    answers = {
        "keyed_correct": NoulAnswer(0.1),
        "exactly_one": NoulAnswer(0.1),
        "unambiguous": NoulAnswer(0.95),
        "aligned": NoulAnswer(0.95),
        "difficulty": ChoiceAnswer(
            choice="easy",
            probabilities={"easy": 0.9, "medium": 0.1, "hard": 0.0},
            confidence=0.8,
        ),
        "explanation_supports": NoulAnswer(0.95),
    }
    outcome = apply_policy(plan, answers, thresholds=_LIMITS)
    assert outcome.decision == DECISION_REJECT
    assert outcome.gate_decision == DECISION_REJECT
    keyed = next(check for check in outcome.checks if check.check_id == "keyed_correct")
    assert keyed.drives_gate is True


def test_coverage_and_difficulty_rules() -> None:
    target = LessonTarget(
        heading="Area",
        grade="Grade 6",
        outcomes=("Find the area.",),
        chunks=("Area is length times width.",),
        markdown="Area is length times width.",
    )
    coverage = next(
        check for check in lesson_plan(target, include_claims=False) if check.rule == "coverage"
    )
    outcome = apply_policy(
        (coverage,),
        {
            "coverage_01": ScoreAnswer(
                score=0.2,
                probabilities={"0": 0.8, "1": 0.2, "2": 0.0, "3": 0.0},
                confidence=0.7,
                legend={"0": "none", "1": "partial", "2": "mostly", "3": "full"},
            )
        },
        thresholds=_LIMITS,
    )
    assert outcome.decision == DECISION_REJECT
    difficulty = next(
        check
        for check in item_plan(
            ItemTarget(
                heading="Area",
                grade="Grade 6",
                outcomes=(),
                chunks=(),
                prompt="Name the shape.",
                options={"A": "Circle", "B": "Rectangle", "C": "Line", "D": "Point"},
                correct_label="B",
                correct_rationale="The stem names a rectangle.",
                difficulty="easy",
            )
        )
        if check.check_id == "difficulty"
    )
    matched = apply_policy(
        (difficulty,),
        {
            "difficulty": ChoiceAnswer(
                choice="hard",
                probabilities={"easy": 0.05, "medium": 0.05, "hard": 0.9},
                confidence=0.9,
            )
        },
        thresholds=_LIMITS,
    )
    assert matched.decision == DECISION_REJECT
    assert "easy" in matched.reasons[0]
