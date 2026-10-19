"""Shadow mode stores a judgment and leaves the gate result unchanged."""

from __future__ import annotations

from uuid import uuid4

from education_platform.modules.generation.item_gate import GatedItem
from education_platform.modules.generation.items import GeneratedItem
from education_platform.modules.generation.types import BloomLevel
from education_platform.modules.judge.checks import ItemTarget
from education_platform.modules.judge.evaluate import Evaluation, apply_to_gate
from education_platform.modules.judge.hook import fold_item
from education_platform.modules.judge.policy import DECISION_REJECT
from education_platform.modules.judge.types import (
    ChoiceAnswer,
    DecisionRequest,
    DecisionResult,
    NoulAnswer,
)


def _generated() -> GeneratedItem:
    return GeneratedItem(
        subtopic_id=uuid4(),
        learning_outcome_ids=(),
        prompt="Which shape has four right angles?",
        options={"A": "Rectangle", "B": "Circle", "C": "Line", "D": "Point"},
        correct_label="A",
        correct_rationale="A rectangle has four right angles.",
        distractor_rationales={
            "B": "A circle has none.",
            "C": "A line has none.",
            "D": "A point has none.",
        },
        bloom=BloomLevel.REMEMBER,
    )


def _gated(
    *, servable: bool = True, reasons: tuple[str, ...] = (), verdict: str = "passed"
) -> GatedItem:
    return GatedItem(
        item=_generated(),
        attempts=1,
        verdict=verdict,
        servable=servable,
        reasons=reasons,
        blind_solve={
            "status": "disagree",
            "answer": "B",
            "working": "The stem sounded like a circle.",
        },
    )


def _rejection() -> Evaluation:
    return Evaluation(
        subject_kind="quiz_item",
        label="Which shape has four right angles?",
        decision=DECISION_REJECT,
        gate_decision=DECISION_REJECT,
        reasons=("Keyed answer is correct: noul 0.10 is at or below 0.35.",),
        gate_reasons=("Keyed answer is correct: noul 0.10 is at or below 0.35.",),
        checks=(),
        model="typesafe/jev-1.14",
        provider="TypeSafe",
        request_id="gen-dec-test",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.0000042,
        latency_ms=40,
        error=None,
        computational=False,
    )


class _RejectingJudge:
    def decide(self, request: DecisionRequest) -> DecisionResult:
        answers: dict[str, NoulAnswer | ChoiceAnswer] = {}
        for check_id, question in request.questions.items():
            kind = question.to_api()["type"]
            if kind == "choice":
                answers[check_id] = ChoiceAnswer(
                    choice="hard",
                    probabilities={"easy": 0.0, "medium": 0.1, "hard": 0.9},
                    confidence=0.9,
                )
            else:
                answers[check_id] = NoulAnswer(0.1)
        return DecisionResult(
            model="typesafe/jev-1.14",
            answers=answers,
            input_tokens=120,
            output_tokens=30,
            cost_usd=0.000005,
            latency_ms=15,
            request_id="gen-dec-shadow",
            provider="TypeSafe",
        )


def _target() -> ItemTarget:
    item = _generated()
    return ItemTarget(
        heading="Shapes",
        grade="Grade 6",
        outcomes=("Identify rectangles.",),
        chunks=("A rectangle has four right angles.",),
        prompt=item.prompt,
        options=item.options,
        correct_label=item.correct_label,
        correct_rationale=item.correct_rationale,
        difficulty="easy",
    )


def test_shadow_mode_does_not_change_the_gate() -> None:
    gated = _gated()
    updated = apply_to_gate(gated, _rejection(), mode="shadow")
    assert updated is gated
    assert updated.servable is True
    assert updated.verdict == "passed"

    folded, evaluation = fold_item(gated, _target(), _RejectingJudge(), mode="shadow")
    assert folded is gated
    assert folded.servable is True
    assert evaluation is not None
    assert evaluation.decision == DECISION_REJECT
    assert any(check.probabilities for check in evaluation.checks)


def test_augment_can_drop_and_replace_keeps_a_solver_owned_computation() -> None:
    gated = _gated()
    dropped = apply_to_gate(gated, _rejection(), mode="augment")
    assert dropped is not gated
    assert dropped.servable is False
    assert dropped.verdict == "dropped"
    assert gated.servable is True

    computational = Evaluation(
        subject_kind="quiz_item",
        label="3 * 4",
        decision=DECISION_REJECT,
        gate_decision="accept",
        reasons=("Keyed answer is correct: noul 0.10 is at or below 0.35.",),
        gate_reasons=(),
        checks=(),
        model="typesafe/jev-1.14",
        provider=None,
        request_id=None,
        input_tokens=10,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=1,
        error=None,
        computational=True,
    )
    kept = apply_to_gate(
        _gated(servable=False, reasons=("blind solve disagreed and chose B",), verdict="dropped"),
        computational,
        mode="replace",
    )
    assert kept.servable is False
    assert kept.verdict == "dropped"


def test_replace_accepts_a_non_computational_item_the_solver_rejected() -> None:
    accepted = Evaluation(
        subject_kind="quiz_item",
        label="shape",
        decision="accept",
        gate_decision="accept",
        reasons=(),
        gate_reasons=(),
        checks=(),
        model="typesafe/jev-1.14",
        provider=None,
        request_id=None,
        input_tokens=10,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=1,
        error=None,
        computational=False,
    )
    gated = _gated(
        servable=False,
        reasons=("blind solve disagreed and chose B",),
        verdict="dropped",
    )
    updated = apply_to_gate(gated, accepted, mode="replace")
    assert updated.servable is True
    assert updated.verdict == "passed"
    assert updated.reasons == ()
