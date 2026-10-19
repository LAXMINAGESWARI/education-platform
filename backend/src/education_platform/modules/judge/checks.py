"""Questions Jev is asked about a lesson section or a quiz item.

State always carries the subtopic chunks and the outline's intended outcomes.
The keyed answer is included for item checks. That is deliberate: this judge
is not the blind solver, which never sees the key.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from education_platform.modules.judge.types import (
    ChoiceQuestion,
    DecisionRequest,
    NoulQuestion,
    PlannedCheck,
    ScoreQuestion,
)

COVERAGE_LEVELS = ("none", "partial", "mostly", "full")
GRADE_LEVELS = (
    "below the stated grade",
    "on the stated grade",
    "above the stated grade",
)
DIFFICULTIES = ("easy", "medium", "hard")
_MAX_CLAIMS = 8
_MAX_OUTCOMES = 6
_PER_CHUNK_CHARS = 4_000
_TOTAL_CHUNK_CHARS = 24_000
_MIN_CLAIM_CHARS = 40
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_DIGIT = re.compile(r"\d")
_OPERATOR = re.compile(r"[=+\-*/^×÷]|\\frac|\\times|\\div|\\pi|π")


@dataclass(frozen=True, slots=True)
class LessonTarget:
    heading: str
    grade: str
    outcomes: tuple[str, ...]
    chunks: tuple[str, ...]
    markdown: str
    kind: str = "lesson_section"


@dataclass(frozen=True, slots=True)
class ItemTarget:
    heading: str
    grade: str
    outcomes: tuple[str, ...]
    chunks: tuple[str, ...]
    prompt: str
    options: dict[str, str]
    correct_label: str
    correct_rationale: str
    difficulty: str


def bound_chunks(chunks: Sequence[str]) -> list[str]:
    kept: list[str] = []
    used = 0
    for raw in chunks:
        text = raw.strip()
        if not text or used >= _TOTAL_CHUNK_CHARS:
            continue
        clipped = text[:_PER_CHUNK_CHARS]
        room = _TOTAL_CHUNK_CHARS - used
        if len(clipped) > room:
            clipped = clipped[:room]
        kept.append(clipped)
        used += len(clipped)
    return kept


def claim_sentences(markdown: str, *, limit: int = _MAX_CLAIMS) -> list[str]:
    """Sentences long enough to be a factual claim. Headings and table rows are skipped."""
    lines: list[str] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "```", "|", "![")):
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        lines.append(line)
    claims: list[str] = []
    for part in _SENTENCE.split(" ".join(lines)):
        cleaned = part.strip()
        if len(cleaned) < _MIN_CLAIM_CHARS:
            continue
        claims.append(cleaned)
        if len(claims) >= limit:
            break
    return claims


def looks_computational(prompt: str, rationale: str, options: Sequence[str]) -> bool:
    """True when the item asks for a calculation the solver should still own."""
    blob = "\n".join((prompt, rationale, *options))
    return _DIGIT.search(blob) is not None and _OPERATOR.search(blob) is not None


def lesson_plan(target: LessonTarget, *, include_claims: bool) -> tuple[PlannedCheck, ...]:
    checks: list[PlannedCheck] = [
        PlannedCheck(
            check_id="grounded",
            rule="noul",
            label="Every claim is supported by the source chunks",
            question=NoulQuestion(
                instructions=(
                    "Is every factual claim in `markdown` supported by `source_chunks`? "
                    "Ignore headings and wording that only organises the section."
                ),
                true_meaning="Each factual claim is supported by the chunks.",
                false_meaning="A factual claim is missing from or contradicted by the chunks.",
            ),
        )
    ]
    if include_claims:
        for index, sentence in enumerate(claim_sentences(target.markdown), start=1):
            checks.append(
                PlannedCheck(
                    check_id=f"claim_{index:02d}",
                    rule="noul",
                    label=sentence,
                    question=NoulQuestion(
                        instructions=(
                            f"Is this sentence supported by `source_chunks`? Sentence: {sentence}"
                        ),
                        true_meaning="The sentence is supported by the chunks.",
                        false_meaning="The sentence adds a fact the chunks do not support.",
                    ),
                )
            )
    for index, outcome in enumerate(_outcomes(target.outcomes), start=1):
        checks.append(
            PlannedCheck(
                check_id=f"coverage_{index:02d}",
                rule="coverage",
                label=outcome,
                question=ScoreQuestion(
                    instructions=(
                        "How fully does `markdown` teach this intended outcome "
                        f"from the outline? Outcome: {outcome}"
                    ),
                    criteria=COVERAGE_LEVELS,
                ),
            )
        )
    checks.append(
        PlannedCheck(
            check_id="grade_level",
            rule="grade",
            label="Grade-level appropriateness",
            question=ScoreQuestion(
                instructions=(
                    "How well does the vocabulary and depth of `markdown` match `grade`?"
                ),
                criteria=GRADE_LEVELS,
            ),
        )
    )
    checks.append(
        PlannedCheck(
            check_id="math_table",
            rule="noul",
            label="Math and tables are correct",
            question=NoulQuestion(
                instructions=(
                    "Is every equation, numeric claim, and table cell in `markdown` "
                    "mathematically consistent with `source_chunks` and internally correct? "
                    "Answer yes when the section has no math and no table."
                ),
                true_meaning="Math and tables are correct, or there are none.",
                false_meaning="A number, equation, or table cell is wrong.",
            ),
        )
    )
    return tuple(checks)


def item_plan(target: ItemTarget) -> tuple[PlannedCheck, ...]:
    stated = target.difficulty if target.difficulty in DIFFICULTIES else "medium"
    return (
        PlannedCheck(
            check_id="keyed_correct",
            rule="noul",
            label="Keyed answer is correct",
            question=NoulQuestion(
                instructions=(
                    "Is the option labelled `keyed_label` the correct answer to `prompt`?"
                ),
                true_meaning="The keyed option is correct.",
                false_meaning="The keyed option is wrong or incomplete.",
            ),
        ),
        PlannedCheck(
            check_id="exactly_one",
            rule="noul",
            label="Exactly one option is correct",
            question=NoulQuestion(
                instructions=(
                    "Considering `prompt` and options A-D, is exactly one option correct?"
                ),
                true_meaning="One option is right and the other three are wrong.",
                false_meaning="Zero options are right, or more than one is right.",
            ),
        ),
        PlannedCheck(
            check_id="unambiguous",
            rule="noul",
            label="Unambiguous and self-contained",
            question=NoulQuestion(
                instructions=(
                    "Is `prompt` unambiguous and self-contained, so a student can answer "
                    "from the prompt and options alone?"
                ),
                true_meaning="The question has one clear reading and needs no missing exhibit.",
                false_meaning="The question is ambiguous or depends on text that is not shown.",
            ),
        ),
        PlannedCheck(
            check_id="aligned",
            rule="noul",
            label="Aligned with the subtopic and chunks",
            question=NoulQuestion(
                instructions=(
                    "Is the question aligned with `subtopic`, `intended_outcomes`, "
                    "and `source_chunks`?"
                ),
                true_meaning="The question assesses the tagged subtopic using the source.",
                false_meaning="The question is off the subtopic or unsupported by the chunks.",
            ),
        ),
        PlannedCheck(
            check_id="difficulty",
            rule="difficulty",
            label="Stated difficulty matches",
            stated=stated,
            question=ChoiceQuestion(
                instructions="Which difficulty matches this item for the stated grade?",
                criteria={
                    "easy": "Recall or a single direct step.",
                    "medium": "Two steps or a routine application.",
                    "hard": "Several steps, a non-routine choice, or careful reasoning.",
                },
            ),
        ),
        PlannedCheck(
            check_id="explanation_supports",
            rule="noul",
            label="Explanation supports the key",
            question=NoulQuestion(
                instructions=(
                    "Does `explanation` support `keyed_option` and avoid contradicting it?"
                ),
                true_meaning="The explanation justifies the keyed option.",
                false_meaning="The explanation is missing or points at another option.",
            ),
        ),
    )


def lesson_request(target: LessonTarget, plan: Sequence[PlannedCheck]) -> DecisionRequest:
    return DecisionRequest(
        state={
            "kind": target.kind,
            "grade": target.grade,
            "subtopic": target.heading,
            "intended_outcomes": list(_outcomes(target.outcomes)),
            "source_chunks": bound_chunks(target.chunks),
            "markdown": target.markdown,
        },
        questions={check.check_id: check.question for check in plan},
    )


def item_request(target: ItemTarget, plan: Sequence[PlannedCheck]) -> DecisionRequest:
    label = target.correct_label
    return DecisionRequest(
        state={
            "kind": "quiz_item",
            "grade": target.grade,
            "subtopic": target.heading,
            "intended_outcomes": list(_outcomes(target.outcomes)),
            "source_chunks": bound_chunks(target.chunks),
            "prompt": target.prompt,
            "options": {
                key: target.options[key] for key in ("A", "B", "C", "D") if key in target.options
            },
            "keyed_label": label,
            "keyed_option": target.options.get(label, ""),
            "explanation": target.correct_rationale,
            "stated_difficulty": target.difficulty,
        },
        questions={check.check_id: check.question for check in plan},
    )


def _outcomes(outcomes: Sequence[str]) -> tuple[str, ...]:
    cleaned = tuple(item.strip() for item in outcomes if item.strip())
    return cleaned[:_MAX_OUTCOMES]
