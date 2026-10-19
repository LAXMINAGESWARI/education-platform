from uuid import uuid4

from education_platform.modules.authoring.service import validate
from education_platform.modules.generation.items import GeneratedItem
from education_platform.modules.generation.latex_text import (
    bare_latex_outside_delimiters,
    normalize_quiz_math,
)
from education_platform.modules.generation.types import BloomLevel
from education_platform.modules.generation.worker import _item_for_storage


def test_wraps_trapezoid_and_volume_formulas() -> None:
    area = r"Area = \frac{1}{2} \times (base_1 + base_2) \times height"
    assert normalize_quiz_math(area) == (
        r"$Area = \frac{1}{2} \times (base_1 + base_2) \times height$"
    )
    volume = r"Volume = \frac{1}{3} \times base \times height"
    assert normalize_quiz_math(volume) == r"$Volume = \frac{1}{3} \times base \times height$"
    assert bare_latex_outside_delimiters(normalize_quiz_math(area)) is False


def test_wraps_division_comparisons_roots_and_scripts() -> None:
    assert normalize_quiz_math(r"Share \div 2") == r"Share $\div 2$"
    assert normalize_quiz_math(r"Use \le, \ge, \neq, and \pi.") == (
        r"Use $\le, \ge, \neq$, and $\pi$."
    )
    assert normalize_quiz_math(r"The length is \sqrt{4} \cdot x^{2} in \text{cm}.") == (
        r"The length is $\sqrt{4} \cdot x^{2}$ in $\text{cm}$."
    )
    assert normalize_quiz_math(r"Label y_{1} on the figure.") == r"Label $y_{1}$ on the figure."


def test_does_not_double_wrap_delimited_math() -> None:
    inline = r"Solve for $x$: $x + 5 = 12$."
    assert normalize_quiz_math(inline) == inline
    mixed = r"Use $\frac{1}{2}$ or \frac{1}{3}."
    assert normalize_quiz_math(mixed) == r"Use $\frac{1}{2}$ or $\frac{1}{3}$."
    display = r"$$\frac{1}{2} \times h$$"
    assert normalize_quiz_math(display) == display
    assert bare_latex_outside_delimiters(display) is False


def test_item_save_and_authoring_validate_wrap_bare_latex() -> None:
    item = GeneratedItem(
        subtopic_id=uuid4(),
        learning_outcome_ids=(),
        prompt=r"Which formula uses \frac{1}{2}?",
        options={
            "A": r"Area = \frac{1}{2} \times (base_1 + base_2) \times height",
            "B": "A circle",
            "C": "A line",
            "D": "A point",
        },
        correct_label="A",
        correct_rationale=r"Multiply by \frac{1}{2}.",
        distractor_rationales={"B": r"That uses \div."},
        bloom=BloomLevel.REMEMBER,
    )
    stored = _item_for_storage(item)
    assert stored.prompt == r"Which formula uses $\frac{1}{2}$?"
    assert stored.options["A"] == (r"$Area = \frac{1}{2} \times (base_1 + base_2) \times height$")
    assert stored.correct_rationale == r"Multiply by $\frac{1}{2}$."
    assert stored.distractor_rationales["B"] == r"That uses $\div$."
    assert bare_latex_outside_delimiters(stored.options["A"]) is False

    draft = validate(
        {
            "prompt": "Which formula gives the area of a trapezoid?",
            "options": {
                "A": r"Area = \frac{1}{2} \times (base_1 + base_2) \times height",
                "B": "A circle",
                "C": "A line",
                "D": "A point",
            },
            "correct": "A",
            "explanation": r"Use \frac{1}{2}.",
        }
    )
    assert not isinstance(draft, str)
    assert draft.options["A"].startswith("$Area")
    assert draft.explanation == r"Use $\frac{1}{2}$."


def test_leaves_plain_sentences_unchanged() -> None:
    sentence = "A rectangle with equal sides"
    assert normalize_quiz_math(sentence) == sentence
    assert bare_latex_outside_delimiters(sentence) is False
