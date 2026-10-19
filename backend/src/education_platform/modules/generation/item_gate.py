"""Retry a failing item with the solver's working, then drop it.

The writer gets at most two retries. A blind-solve disagreement, an error, a
choice of "none of these is correct", a failed numeric check, or a duplicate
stem blocks the item. After the last retry the item is not shippable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from education_platform.modules.assessments.prompt_identity import same_stem
from education_platform.modules.generation.item_checks import numeric_problems, structural_problems
from education_platform.modules.generation.items import GeneratedItem

MAX_RETRIES = 2

Rewrite = Callable[[GeneratedItem, str], GeneratedItem | None]
Solve = Callable[[GeneratedItem], Mapping[str, object] | None]


@dataclass(frozen=True, slots=True)
class GatedItem:
    item: GeneratedItem
    attempts: int
    verdict: str
    servable: bool
    reasons: tuple[str, ...]
    blind_solve: dict[str, object] | None

    def rubric(self) -> dict[str, object]:
        return {
            "attempts": self.attempts,
            "verdict": self.verdict,
            "servable": self.servable,
            "reasons": list(self.reasons),
        }


def gate_generated_item(
    item: GeneratedItem,
    *,
    solve: Solve | None,
    rewrite: Rewrite | None,
    taken: list[str],
) -> GatedItem:
    """Review ``item``. ``taken`` collects stems already kept or dropped."""
    current = item
    reasons: tuple[str, ...] = ()
    blind: dict[str, object] | None = None
    attempt = 1
    for attempt in range(1, MAX_RETRIES + 2):
        reasons, blind = _review(current, solve=solve, taken=taken)
        if not reasons:
            taken.append(current.prompt)
            verdict = "passed" if attempt == 1 else "fixed_on_retry"
            return GatedItem(
                item=current,
                attempts=attempt,
                verdict=verdict,
                servable=True,
                reasons=(),
                blind_solve=blind,
            )
        if attempt > MAX_RETRIES or rewrite is None:
            break
        revised = rewrite(current, _feedback(current, reasons, blind))
        if revised is None:
            break
        current = revised
    taken.append(current.prompt)
    return GatedItem(
        item=current,
        attempts=attempt,
        verdict="dropped",
        servable=False,
        reasons=reasons,
        blind_solve=blind,
    )


def _review(
    item: GeneratedItem,
    *,
    solve: Solve | None,
    taken: Sequence[str],
) -> tuple[tuple[str, ...], dict[str, object] | None]:
    reasons = list(structural_problems(item.options, item.correct_label, item.correct_rationale))
    reasons.extend(numeric_problems(item.options, item.correct_label, item.correct_rationale))
    if any(same_stem(item.prompt, previous) for previous in taken):
        reasons.append("duplicate of another item in the bank")
    if reasons or solve is None:
        return tuple(reasons), None
    blind_raw = solve(item)
    blind = dict(blind_raw) if blind_raw is not None else None
    if blind is None:
        return tuple(reasons), None
    status = blind.get("status")
    answer = blind.get("answer")
    if answer == "E":
        reasons.append("blind solve chose none of these is correct")
    elif status == "disagree":
        reasons.append(f"blind solve disagreed and chose {answer}")
    elif status == "error":
        reasons.append("blind solve errored")
    return tuple(reasons), blind


def _feedback(
    item: GeneratedItem,
    reasons: Sequence[str],
    blind: Mapping[str, object] | None,
) -> str:
    raw_working = blind.get("working") if blind is not None else None
    working = raw_working if isinstance(raw_working, str) else ""
    lines = [
        "The previous item failed validation. Fix it. Do not repeat the same mistake.",
        f"Prompt: {item.prompt}",
        f"Keyed option {item.correct_label}: {item.options.get(item.correct_label, '')}",
        f"Rationale: {item.correct_rationale}",
        "Failures: " + "; ".join(reasons),
    ]
    if working:
        lines.append(f"Solver working: {working}")
    lines.append(
        "Compute the answer from the stem before writing options. "
        "The keyed option must be that answer, and it must appear among A-D."
    )
    return "\n".join(lines)
