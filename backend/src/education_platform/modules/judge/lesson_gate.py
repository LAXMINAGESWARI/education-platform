"""One regeneration for a section Jev says is ungrounded or off the source.

A second failure marks the lesson as needing review. Grade-level and math
rejects are stored and do not regenerate the slide or hold publish. A Jev
error keeps the section that was already written.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from education_platform.modules.judge.checks import LessonTarget
from education_platform.modules.judge.evaluate import Evaluation, evaluate_lesson
from education_platform.modules.judge.policy import DECISION_ERROR, DECISION_REJECT, DECISION_REVIEW
from education_platform.modules.judge.protocol import Judge

logger = logging.getLogger(__name__)

RewriteSection = Callable[[LessonTarget, tuple[str, ...]], str | None]


@dataclass(frozen=True, slots=True)
class SectionReview:
    target: LessonTarget
    evaluation: Evaluation | None
    regenerated: bool


def source_rejected(evaluation: Evaluation) -> bool:
    """True when a driving grounded, claim, or coverage check rejected."""
    if evaluation.decision == DECISION_ERROR:
        return False
    return any(
        check.drives_gate and check.decision == DECISION_REJECT and _source_check(check.check_id)
        for check in evaluation.checks
    )


def lesson_hold(reasons: tuple[str, ...], *, model: str | None) -> Evaluation:
    """Lesson-level row the step panel and the publish gate both read."""
    return Evaluation(
        subject_kind="lesson",
        label="Lesson needs review",
        decision=DECISION_REVIEW,
        gate_decision=DECISION_REVIEW,
        reasons=reasons,
        gate_reasons=reasons,
        checks=(),
        model=model,
        provider=None,
        request_id=None,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=0,
        error=None,
        computational=False,
    )


def review_lesson_sections(
    targets: Sequence[LessonTarget],
    judge: Judge | None,
    *,
    mode: str,
    rewrite: RewriteSection | None,
) -> tuple[tuple[SectionReview, ...], bool]:
    """Judge each section. Enforce mode rewrites a source reject once.

    The boolean is true when a section is still ungrounded or off-source after
    that rewrite, so publish waits for the existing QA accept.
    """
    if judge is None or mode == "off":
        return tuple(SectionReview(target, None, False) for target in targets), False
    held = False
    reviews: list[SectionReview] = []
    for target in targets:
        evaluation = _safe_lesson(target, judge, include_claims=True)
        regenerated = False
        current = target
        if mode == "enforce" and evaluation is not None and source_rejected(evaluation):
            revised = _rewrite(current, evaluation, rewrite)
            if revised is None:
                held = True
            else:
                current = revised
                regenerated = True
                evaluation = _safe_lesson(current, judge, include_claims=True)
                if evaluation is None or source_rejected(evaluation):
                    held = True
        reviews.append(SectionReview(current, evaluation, regenerated))
    return tuple(reviews), held


def _rewrite(
    target: LessonTarget,
    evaluation: Evaluation,
    rewrite: RewriteSection | None,
) -> LessonTarget | None:
    if rewrite is None:
        return None
    try:
        markdown = rewrite(target, evaluation.gate_reasons or evaluation.reasons)
    except Exception:
        logger.warning("Jev lesson rewrite failed open", exc_info=True)
        return None
    if markdown is None or not markdown.strip():
        return None
    return LessonTarget(
        heading=target.heading,
        grade=target.grade,
        outcomes=target.outcomes,
        chunks=target.chunks,
        markdown=markdown,
        kind=target.kind,
    )


def _safe_lesson(target: LessonTarget, judge: Judge, *, include_claims: bool) -> Evaluation | None:
    try:
        evaluation = evaluate_lesson(target, judge, include_claims=include_claims)
    except Exception:
        logger.warning("Jev lesson judge failed open", exc_info=True)
        return None
    if evaluation.decision == DECISION_ERROR:
        logger.warning("Jev lesson judge unavailable: %s", evaluation.error)
    return evaluation


def _source_check(check_id: str) -> bool:
    return (
        check_id == "grounded" or check_id.startswith("claim_") or check_id.startswith("coverage_")
    )
