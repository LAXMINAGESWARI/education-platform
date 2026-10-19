"""Compare Jev verdicts with the blind-solve gate.

Run from ``backend/``::

    uv run python -m education_platform.modules.judge.compare bank.json
    uv run python -m education_platform.modules.judge.compare --run-id <uuid>
    uv run python -m education_platform.modules.judge.compare --run-id <uuid> --stored

``bank.json`` has ``lessons`` and ``items``. ``--stored`` reads judgments already
saved for that run and does not call Jev. A live run uses ``OPENROUTER_API_KEY``
and does not print it.

Agreement counts an item only when the blind solver agreed or disagreed and Jev
accepted or rejected. Needs-review and solver errors are listed apart from that
rate. Jev does not write a prose rationale; the listed reason is the threshold
that fired. The solver rationale is its stored working.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from education_platform.modules.judge.checks import ItemTarget, LessonTarget
from education_platform.modules.judge.client import INPUT_USD_PER_MILLION, estimate_cost_usd
from education_platform.modules.judge.evaluate import (
    Evaluation,
    evaluate_item,
    evaluate_lesson,
    open_judge,
)
from education_platform.modules.judge.protocol import Judge

_AGREE = frozenset({"agree"})
_DISAGREE = frozenset({"disagree"})
_JEV_CLOSED = frozenset({"accept", "reject"})


@dataclass(frozen=True, slots=True)
class BankLesson:
    key: str
    target: LessonTarget


@dataclass(frozen=True, slots=True)
class BankItem:
    key: str
    target: ItemTarget
    blind_status: str | None
    blind_answer: str | None
    blind_working: str


@dataclass(frozen=True, slots=True)
class Disagreement:
    key: str
    prompt: str
    jev_decision: str
    jev_reasons: tuple[str, ...]
    blind_status: str
    blind_answer: str | None
    blind_working: str


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    lessons: tuple[tuple[str, str, tuple[str, ...]], ...]
    item_count: int
    comparable: int
    agreements: int
    disagreements: tuple[Disagreement, ...]
    needs_review: int
    blind_unscored: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int

    @property
    def agreement_rate(self) -> float | None:
        if self.comparable == 0:
            return None
        return self.agreements / self.comparable


def compare_bank(
    lessons: Sequence[BankLesson],
    items: Sequence[BankItem],
    judge: Judge,
) -> ComparisonReport:
    lesson_rows: list[tuple[str, str, tuple[str, ...]]] = []
    disagreements: list[Disagreement] = []
    comparable = 0
    agreements = 0
    needs_review = 0
    blind_unscored = 0
    input_tokens = 0
    output_tokens = 0
    cost = 0.0
    latency = 0
    for lesson in lessons:
        evaluation = evaluate_lesson(
            lesson.target, judge, include_claims=lesson.target.kind != "lesson"
        )
        lesson_rows.append((lesson.key, evaluation.decision, evaluation.reasons))
        input_tokens, output_tokens, cost, latency = _add(
            evaluation, input_tokens, output_tokens, cost, latency
        )
    for item in items:
        evaluation = evaluate_item(item.target, judge)
        input_tokens, output_tokens, cost, latency = _add(
            evaluation, input_tokens, output_tokens, cost, latency
        )
        pair = score_pair(item, evaluation.decision, evaluation.reasons)
        needs_review += int(pair.needs_review)
        blind_unscored += int(pair.blind_unscored)
        comparable += int(pair.comparable)
        agreements += int(pair.agreed)
        if pair.disagreement is not None:
            disagreements.append(pair.disagreement)
    return ComparisonReport(
        lessons=tuple(lesson_rows),
        item_count=len(items),
        comparable=comparable,
        agreements=agreements,
        disagreements=tuple(disagreements),
        needs_review=needs_review,
        blind_unscored=blind_unscored,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        latency_ms=latency,
    )


@dataclass(frozen=True, slots=True)
class PairScore:
    comparable: bool
    agreed: bool
    needs_review: bool
    blind_unscored: bool
    disagreement: Disagreement | None


def score_pair(item: BankItem, decision: str, reasons: Sequence[str]) -> PairScore:
    status = item.blind_status
    needs_review = decision == "needs_review"
    blind_unscored = status not in _AGREE | _DISAGREE
    if blind_unscored or decision not in _JEV_CLOSED:
        return PairScore(
            comparable=False,
            agreed=False,
            needs_review=needs_review,
            blind_unscored=blind_unscored,
            disagreement=None,
        )
    matched = (status in _AGREE and decision == "accept") or (
        status in _DISAGREE and decision == "reject"
    )
    disagreement = None
    if not matched:
        disagreement = Disagreement(
            key=item.key,
            prompt=_excerpt(item.target.prompt),
            jev_decision=decision,
            jev_reasons=tuple(reasons),
            blind_status=status or "",
            blind_answer=item.blind_answer,
            blind_working=item.blind_working,
        )
    return PairScore(
        comparable=True,
        agreed=matched,
        needs_review=needs_review,
        blind_unscored=False,
        disagreement=disagreement,
    )


def render_report(report: ComparisonReport) -> str:
    rate = "n/a" if report.agreement_rate is None else f"{report.agreement_rate:.1%}"
    lines = [
        "# Jev comparison",
        "",
        f"Items: {report.item_count}",
        f"Comparable with the blind solver: {report.comparable}",
        f"Agreements: {report.agreements}",
        f"Agreement rate: {rate}",
        f"Needs review: {report.needs_review}",
        f"Blind solver unscored (error, skipped, or missing): {report.blind_unscored}",
        f"Input tokens: {report.input_tokens}",
        f"Output tokens: {report.output_tokens}",
        f"Cost USD: {report.cost_usd:.6f}",
        f"Latency ms: {report.latency_ms}",
        "",
        "## Lessons",
    ]
    if not report.lessons:
        lines.append("No lesson targets.")
    for key, decision, reasons in report.lessons:
        lines.append(f"- {key}: {decision}")
        lines.extend(f"  - {reason}" for reason in reasons)
    lines.extend(["", "## Disagreements"])
    if not report.disagreements:
        lines.append("None.")
    for row in report.disagreements:
        lines.append(f"- {row.key}: Jev {row.jev_decision}, blind solver {row.blind_status}")
        lines.append(f"  - Prompt: {row.prompt}")
        if row.blind_answer:
            lines.append(f"  - Blind answer: {row.blind_answer}")
        if row.blind_working:
            lines.append(f"  - Solver working: {row.blind_working}")
        if row.jev_reasons:
            lines.extend(f"  - Jev: {reason}" for reason in row.jev_reasons)
        else:
            lines.append("  - Jev: no threshold reason was stored.")
    lines.extend(["", "## Budget", budget_note()])
    return "\n".join(lines)


def budget_note() -> str:
    """Illustrative cost at the published input price. Output tokens are free."""
    # cl100k counts of the JSON body for a two-chunk section, an eight-chunk
    # lesson, and one four-option item. Jev's own tokenizer can differ slightly.
    section_tokens = 2_350
    lesson_tokens = 7_300
    sections = 8
    item_tokens = 1_700
    bank = 50
    lesson_total = sections * section_tokens + lesson_tokens
    bank_total = bank * item_tokens
    return (
        f"Published price is ${INPUT_USD_PER_MILLION:.3f} per 1M input tokens; output is free. "
        f"A lesson of {sections} sections at ~{section_tokens} input tokens each, plus one "
        f"whole-lesson call at ~{lesson_tokens}, is about "
        f"${estimate_cost_usd(lesson_total):.4f}. "
        f"A {bank}-item bank at ~{item_tokens} input tokens each is about "
        f"${estimate_cost_usd(bank_total):.4f}. "
        "Real totals follow the usage fields above when a call was made."
    )


def load_bank_json(path: Path) -> tuple[list[BankLesson], list[BankItem]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("The bank file must be a JSON object.")
    lessons_raw = raw.get("lessons", [])
    items_raw = raw.get("items", [])
    if not isinstance(lessons_raw, list) or not isinstance(items_raw, list):
        raise ValueError("lessons and items must be arrays.")
    lessons = [_lesson(item) for item in lessons_raw]
    items = [_item(item) for item in items_raw]
    return lessons, items


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare Jev judgments with the blind solver.")
    parser.add_argument("bank", nargs="?", type=Path, help="Exported lessons and items JSON.")
    parser.add_argument("--run-id", type=UUID, default=None, help="Generation run to load.")
    parser.add_argument(
        "--stored",
        action="store_true",
        help="Compare judgments already stored for --run-id. Does not call Jev.",
    )
    args = parser.parse_args(argv)
    if args.stored:
        if args.run_id is None:
            parser.error("--stored requires --run-id")
        report = _stored_report(args.run_id)
    elif args.run_id is not None:
        lessons, items = _load_run(args.run_id)
        report = compare_bank(lessons, items, _live_judge())
    elif args.bank is not None:
        lessons, items = load_bank_json(args.bank)
        report = compare_bank(lessons, items, _live_judge())
    else:
        parser.error("Pass a bank JSON path or --run-id")
    print(render_report(report))
    return 0


def _live_judge() -> Judge:
    judge = open_judge()
    if judge is None:
        raise SystemExit(
            "Jev is off or OPENROUTER_API_KEY is not configured. "
            "Use --stored to compare judgments already saved for a run."
        )
    return judge


def _lesson(raw: object) -> BankLesson:
    data = _object(raw, "lesson")
    kind = str(data.get("kind") or "lesson_section")
    if kind not in {"lesson_section", "lesson"}:
        kind = "lesson_section"
    target = LessonTarget(
        heading=_text(data, "heading"),
        grade=_text(data, "grade"),
        outcomes=_texts(data.get("outcomes")),
        chunks=_texts(data.get("chunks")),
        markdown=_text(data, "markdown"),
        kind=kind,
    )
    return BankLesson(key=_text(data, "id") or target.heading, target=target)


def _item(raw: object) -> BankItem:
    data = _object(raw, "item")
    options = data.get("options")
    if not isinstance(options, dict):
        raise ValueError("Each item needs an options object.")
    cleaned = {str(key): str(value) for key, value in options.items()}
    blind = data.get("blind_solve")
    status = None
    answer = None
    working = ""
    if isinstance(blind, dict):
        raw_status = blind.get("status")
        raw_answer = blind.get("answer")
        raw_working = blind.get("working")
        status = raw_status if isinstance(raw_status, str) else None
        answer = raw_answer if isinstance(raw_answer, str) else None
        working = raw_working if isinstance(raw_working, str) else ""
    target = ItemTarget(
        heading=_text(data, "heading"),
        grade=_text(data, "grade"),
        outcomes=_texts(data.get("outcomes")),
        chunks=_texts(data.get("chunks")),
        prompt=_text(data, "prompt"),
        options=cleaned,
        correct_label=_text(data, "correct_label"),
        correct_rationale=_text(data, "correct_rationale"),
        difficulty=_text(data, "difficulty") or "medium",
    )
    return BankItem(
        key=_text(data, "id") or target.prompt[:40],
        target=target,
        blind_status=status,
        blind_answer=answer,
        blind_working=working,
    )


def _object(raw: object, name: str) -> Mapping[str, object]:
    if not isinstance(raw, dict):
        raise ValueError(f"Each {name} must be an object.")
    return raw


def _text(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _texts(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())


def _excerpt(prompt: str) -> str:
    text = " ".join(prompt.split())
    if len(text) > 180:
        return text[:180] + "..."
    return text


def _add(
    evaluation: Evaluation,
    input_tokens: int,
    output_tokens: int,
    cost: float,
    latency: int,
) -> tuple[int, int, float, int]:
    return (
        input_tokens + evaluation.input_tokens,
        output_tokens + evaluation.output_tokens,
        cost + evaluation.cost_usd,
        latency + evaluation.latency_ms,
    )


def _load_run(run_id: UUID) -> tuple[list[BankLesson], list[BankItem]]:
    # bank_load imports the report types defined in this module.
    from education_platform.modules.judge.bank_load import load_run_bank

    return load_run_bank(run_id)


def _stored_report(run_id: UUID) -> ComparisonReport:
    # bank_load imports the report types defined in this module.
    from education_platform.modules.judge.bank_load import load_stored_report

    return load_stored_report(run_id)


if __name__ == "__main__":
    sys.exit(main())
