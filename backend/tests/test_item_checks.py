"""Structural and numeric gates, including the cylinder item from production."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from education_platform.modules.generation.item_checks import (
    evaluate_expression,
    numeric_problems,
    structural_problems,
)
from education_platform.modules.generation.item_gate import gate_generated_item
from education_platform.modules.generation.items import GeneratedItem
from education_platform.modules.generation.types import BloomLevel

_CYLINDER = "If a cylinder has a radius of 3 cm and a height of 5 cm, what is the volume?"
_OPTIONS = {
    "A": r"$27\pi$",
    "B": r"$15\pi$",
    "C": r"$36\pi$",
    "D": r"$18\pi$",
}
_RATIONALE = r"V = $\pi(3)^2(5) = 27\pi$"


def test_cylinder_rationale_is_not_27_pi() -> None:
    assert evaluate_expression(r"\pi(3)^2(5)") == evaluate_expression("45π")
    assert numeric_problems(_OPTIONS, "A", _RATIONALE)


def test_matching_arithmetic_and_key_passes() -> None:
    options = {"A": "45π", "B": "15π", "C": "36π", "D": "18π"}
    rationale = "V = π(3)^2(5) = 45π"
    assert numeric_problems(options, "A", rationale) == ()
    assert structural_problems(options, "A", rationale) == ()


def test_explanation_final_value_must_match_the_keyed_option() -> None:
    options = {"A": "$45\\pi$", "B": "$15\\pi$", "C": "$36\\pi$", "D": "$18\\pi$"}
    reasons = structural_problems(options, "B", "The volume is $45\\pi$.")
    assert any("keyed option" in reason for reason in reasons)


def test_equation_steps_are_not_the_keyed_option() -> None:
    options = {"A": "$x = 5$", "B": "$x = 2$", "C": "$x = 7$", "D": "$x = 3$"}
    rationale = "Start from $2x - 3 = 7$. Add 3 to get $2x = 10$. Divide by 2 to get $x = 5$."
    assert structural_problems(options, "A", rationale) == ()
    assert numeric_problems(options, "A", rationale) == ()


def test_solution_matches_a_bare_number_or_the_assignment() -> None:
    options = {"A": "$5$", "B": "$2$", "C": "$7$", "D": "$3$"}
    assert structural_problems(options, "A", "The solution is $x = 5$.") == ()
    assigned = {"A": "$x = 5$", "B": "$x = 2$", "C": "$x = 7$", "D": "$x = 3$"}
    assert structural_problems(assigned, "A", "The solution is $5$.") == ()
    wrong = structural_problems(assigned, "D", "The solution is $x = 5$.")
    assert any("keyed option" in reason for reason in wrong)


def test_operation_choice_is_not_compared_to_the_equation() -> None:
    options = {
        "A": "Add 3 to both sides",
        "B": "Subtract 2 from both sides",
        "C": "Multiply both sides by 2",
        "D": "Divide both sides by 3",
    }
    rationale = "The equation $2x - 3 = 7$ becomes simpler after adding 3."
    assert structural_problems(options, "A", rationale) == ()


def test_options_must_be_distinct_after_normalizing_latex() -> None:
    options = {"A": r"$27\pi$", "B": "27π", "C": "36π", "D": "18π"}
    reasons = structural_problems(options, "A", "The volume is $27\\pi$.")
    assert any("distinct" in reason for reason in reasons)


def _item(rationale: str, prompt: str = _CYLINDER) -> GeneratedItem:
    return GeneratedItem(
        subtopic_id=uuid4(),
        learning_outcome_ids=(),
        prompt=prompt,
        options=dict(_OPTIONS),
        correct_label="A",
        correct_rationale=rationale,
        distractor_rationales={"B": "too small", "C": "uses diameter", "D": "drops a factor"},
        bloom=BloomLevel.APPLY,
    )


def test_numeric_failure_retries_twice_then_drops() -> None:
    seen: list[str] = []

    def rewrite(item: GeneratedItem, note: str) -> GeneratedItem:
        seen.append(note)
        return item

    gated = gate_generated_item(_item(_RATIONALE), solve=None, rewrite=rewrite, taken=[])
    assert gated.servable is False
    assert gated.verdict == "dropped"
    assert gated.attempts == 3
    assert len(seen) == 2
    assert "π(3)^2(5)" in seen[0] or "rationale arithmetic" in seen[0]


def test_blind_disagree_and_none_correct_block_the_item() -> None:
    def solve(item: GeneratedItem) -> Mapping[str, object]:
        del item
        return {
            "model": "openai/gpt-4o",
            "answer": "E",
            "agrees": False,
            "confidence": 0.4,
            "status": "disagree",
            "working": "45π is not listed",
        }

    notes: list[str] = []

    def rewrite(item: GeneratedItem, note: str) -> GeneratedItem:
        notes.append(note)
        return item

    good = GeneratedItem(
        subtopic_id=uuid4(),
        learning_outcome_ids=(),
        prompt="Which claim about amber birch follows?",
        options={"A": "yes", "B": "no", "C": "maybe", "D": "later"},
        correct_label="A",
        correct_rationale="Option A states the relationship correctly.",
        distractor_rationales={"B": "no", "C": "no", "D": "no"},
        bloom=BloomLevel.REMEMBER,
    )
    gated = gate_generated_item(good, solve=solve, rewrite=rewrite, taken=[])
    assert gated.verdict == "dropped"
    assert gated.attempts == 3
    assert "none of these is correct" in " ".join(gated.reasons)
    assert "45π is not listed" in notes[0]


def test_agreeing_solver_passes_without_retry() -> None:
    def solve(_item: GeneratedItem) -> Mapping[str, object]:
        return {"status": "agree", "answer": "A", "working": "checked"}

    calls = {"n": 0}

    def rewrite(item: GeneratedItem, note: str) -> GeneratedItem:
        del note
        calls["n"] += 1
        return item

    item = _item("Option A states the relationship correctly.", prompt="Unique amber prompt")
    gated = gate_generated_item(item, solve=solve, rewrite=rewrite, taken=[])
    assert gated.servable is True
    assert gated.verdict == "passed"
    assert gated.attempts == 1
    assert calls["n"] == 0
