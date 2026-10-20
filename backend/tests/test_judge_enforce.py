"""Enforce mode retries items, holds a lesson, and fails open when Jev is down."""

from __future__ import annotations

from uuid import uuid4

from education_platform.modules.generation.item_gate import (
    JudgeAttempt,
    gate_generated_item,
)
from education_platform.modules.generation.items import GeneratedItem
from education_platform.modules.generation.types import BloomLevel
from education_platform.modules.judge.checks import LessonTarget
from education_platform.modules.judge.errors import JudgeError, JudgePaymentRequired
from education_platform.modules.judge.evaluate import evaluate_item
from education_platform.modules.judge.lesson_gate import review_lesson_sections, source_rejected
from education_platform.modules.judge.policy import DECISION_ERROR, DECISION_REJECT
from education_platform.modules.judge.types import (
    CheckResult,
    DecisionRequest,
    DecisionResult,
    NoulAnswer,
    ScoreAnswer,
    ScoreQuestion,
)


def _item(prompt: str = "Which shape has four right angles?") -> GeneratedItem:
    return GeneratedItem(
        subtopic_id=uuid4(),
        learning_outcome_ids=(),
        prompt=prompt,
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


def _reject(reason: str = "Jev: the key is not supported.") -> JudgeAttempt:
    return JudgeAttempt(decision="reject", reasons=(reason,))


def test_enforced_reject_retries_then_drops() -> None:
    seen: list[str] = []

    def rewrite(item: GeneratedItem, note: str) -> GeneratedItem:
        seen.append(note)
        return _item(prompt=f"{item.prompt} revised {len(seen)}")

    gated = gate_generated_item(
        _item(),
        solve=lambda _item: {"status": "agree", "answer": "A", "working": "Four right angles."},
        rewrite=rewrite,
        taken=[],
        judge=lambda _item: _reject(),
        enforce_judge=True,
    )
    assert gated.servable is False
    assert gated.verdict == "dropped"
    assert gated.attempts == 3
    assert len(seen) == 2
    assert "Jev: the key is not supported." in seen[0]
    assert "blind solve" not in seen[0]


def test_enforced_reject_can_pass_on_retry_when_both_agree() -> None:
    calls = {"n": 0}

    def judge(_item: GeneratedItem) -> JudgeAttempt:
        calls["n"] += 1
        if calls["n"] == 1:
            return _reject()
        return JudgeAttempt(decision="accept")

    gated = gate_generated_item(
        _item(),
        solve=lambda _item: {"status": "agree", "answer": "A", "working": ""},
        rewrite=lambda item, _note: _item(prompt=item.prompt + " fixed"),
        taken=[],
        judge=judge,
        enforce_judge=True,
    )
    assert gated.servable is True
    assert gated.verdict == "fixed_on_retry"
    assert gated.needs_review is False
    assert calls["n"] == 2


def test_needs_review_is_kept_and_flagged() -> None:
    gated = gate_generated_item(
        _item(),
        solve=lambda _item: {"status": "agree", "answer": "A", "working": ""},
        rewrite=lambda _item, _note: None,
        taken=[],
        judge=lambda _item: JudgeAttempt(decision="needs_review", needs_review=True),
        enforce_judge=True,
    )
    assert gated.servable is True
    assert gated.verdict == "passed"
    assert gated.needs_review is True
    assert gated.rubric()["needs_review"] is True


def test_shadow_reject_does_not_retry() -> None:
    calls = {"rewrite": 0}

    def rewrite(item: GeneratedItem, _note: str) -> GeneratedItem:
        calls["rewrite"] += 1
        return item

    gated = gate_generated_item(
        _item(),
        solve=lambda _item: {"status": "agree", "answer": "A", "working": ""},
        rewrite=rewrite,
        taken=[],
        judge=lambda _item: _reject(),
        enforce_judge=False,
    )
    assert gated.servable is True
    assert gated.verdict == "passed"
    assert calls["rewrite"] == 0


def test_blind_solve_and_jev_must_both_pass() -> None:
    gated = gate_generated_item(
        _item(),
        solve=lambda _item: {"status": "disagree", "answer": "B", "working": "Looked round."},
        rewrite=None,
        taken=[],
        judge=lambda _item: JudgeAttempt(decision="accept"),
        enforce_judge=True,
    )
    assert gated.servable is False
    assert any("blind solve" in reason for reason in gated.reasons)


def _target() -> object:
    item = _item()
    from education_platform.modules.judge.checks import ItemTarget

    return ItemTarget(
        heading="Shapes",
        grade="Grade 6",
        outcomes=(),
        chunks=("A rectangle has four right angles.",),
        prompt=item.prompt,
        options=dict(item.options),
        correct_label=item.correct_label,
        correct_rationale=item.correct_rationale,
        difficulty="easy",
    )


def test_payment_required_and_timeout_fail_open() -> None:
    from education_platform.modules.judge.checks import ItemTarget

    target = _target()
    assert isinstance(target, ItemTarget)

    class _Pay:
        def decide(self, _request: DecisionRequest) -> DecisionResult:
            raise JudgePaymentRequired("OpenRouter returned HTTP 402 Payment Required.")

    class _Timeout:
        def decide(self, _request: DecisionRequest) -> DecisionResult:
            raise JudgeError("Jev request timed out.")

    paid = evaluate_item(target, _Pay())
    timed = evaluate_item(target, _Timeout())
    assert paid.decision == DECISION_ERROR
    assert "402" in (paid.error or "")
    assert timed.decision == DECISION_ERROR
    assert "timed out" in (timed.error or "")

    gated = gate_generated_item(
        _item(),
        solve=lambda _item: {"status": "agree", "answer": "A", "working": ""},
        rewrite=lambda _item, _note: None,
        taken=[],
        judge=lambda _item: JudgeAttempt(decision="error"),
        enforce_judge=True,
    )
    assert gated.servable is True
    assert gated.verdict == "passed"


def _section(markdown: str) -> LessonTarget:
    return LessonTarget(
        heading="Area",
        grade="Grade 6",
        outcomes=("Find the area.",),
        chunks=("Area is length times width.",),
        markdown=markdown,
        kind="lesson_section",
    )


def _lesson_result(request: DecisionRequest, *, grounded: float) -> DecisionResult:
    answers: dict[str, NoulAnswer | ScoreAnswer] = {}
    for key, question in request.questions.items():
        if isinstance(question, ScoreQuestion):
            if key.startswith("coverage") and grounded < 0.5:
                answers[key] = ScoreAnswer(
                    score=0.0,
                    probabilities={"0": 0.9, "1": 0.1, "2": 0.0, "3": 0.0},
                    confidence=0.9,
                    legend=None,
                )
            else:
                answers[key] = ScoreAnswer(
                    score=1.0,
                    probabilities={"0": 0.05, "1": 0.9, "2": 0.05},
                    confidence=0.9,
                    legend=None,
                )
        else:
            low = key == "grounded" or key.startswith("claim_") or key.startswith("coverage_")
            answers[key] = NoulAnswer(grounded if low else 0.95)
    return DecisionResult(
        model="typesafe/jev-1.14",
        answers=answers,
        input_tokens=10,
        output_tokens=1,
        cost_usd=0.0,
        latency_ms=1,
        request_id=None,
        provider="TypeSafe",
    )


class _ScriptedLessonJudge:
    def __init__(self, steps: list[float | Exception]) -> None:
        self._steps = steps

    def decide(self, request: DecisionRequest) -> DecisionResult:
        nxt = self._steps.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return _lesson_result(request, grounded=nxt)


def test_source_reject_rewrites_once_and_holds_publish() -> None:
    rewrites: list[tuple[str, ...]] = []

    def rewrite(target: LessonTarget, reasons: tuple[str, ...]) -> str:
        rewrites.append(reasons)
        return target.markdown + "\n\nRevised from the source."

    judge = _ScriptedLessonJudge([0.1, 0.1])
    reviews, held = review_lesson_sections(
        (_section("The area is always 12."),),
        judge,
        mode="enforce",
        rewrite=rewrite,
    )
    assert held is True
    assert len(rewrites) == 1
    assert reviews[0].regenerated is True
    assert reviews[0].evaluation is not None
    assert source_rejected(reviews[0].evaluation)
    assert "Revised from the source." in reviews[0].target.markdown


def test_source_reject_that_passes_on_rewrite_does_not_hold() -> None:
    judge = _ScriptedLessonJudge([0.1, 0.95])
    reviews, held = review_lesson_sections(
        (_section("Area is length times width."),),
        judge,
        mode="enforce",
        rewrite=lambda target, _reasons: target.markdown + " grounded",
    )
    assert held is False
    assert reviews[0].regenerated is True
    assert reviews[0].evaluation is not None
    assert reviews[0].evaluation.decision != DECISION_REJECT


def test_lesson_timeout_does_not_rewrite_or_hold() -> None:
    judge = _ScriptedLessonJudge([JudgeError("Jev request timed out.")])
    _reviews, held = review_lesson_sections(
        (_section("Area is length times width."),),
        judge,
        mode="enforce",
        rewrite=lambda _target, _reasons: "should not run",
    )
    assert held is False


def test_shadow_lesson_does_not_rewrite() -> None:
    calls = {"n": 0}

    def rewrite(target: LessonTarget, _reasons: tuple[str, ...]) -> str:
        calls["n"] += 1
        return target.markdown

    reviews, held = review_lesson_sections(
        (_section("Area is length times width."),),
        _ScriptedLessonJudge([0.1]),
        mode="shadow",
        rewrite=rewrite,
    )
    assert held is False
    assert calls["n"] == 0
    assert reviews[0].evaluation is not None
    assert source_rejected(reviews[0].evaluation)


def test_grade_reject_does_not_count_as_a_source_hold() -> None:
    evaluation_checks = (
        CheckResult(
            check_id="grade_level",
            kind="score",
            decision=DECISION_REJECT,
            summary="below",
            reasons=("Grade level is below.",),
            probabilities={"0": 0.8},
            drives_gate=True,
        ),
    )
    from education_platform.modules.judge.evaluate import Evaluation

    evaluation = Evaluation(
        subject_kind="lesson_section",
        label="Area",
        decision=DECISION_REJECT,
        gate_decision=DECISION_REJECT,
        reasons=("Grade level is below.",),
        gate_reasons=("Grade level is below.",),
        checks=evaluation_checks,
        model="typesafe/jev-1.14",
        provider=None,
        request_id=None,
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.0,
        latency_ms=1,
        error=None,
        computational=False,
    )
    assert source_rejected(evaluation) is False
