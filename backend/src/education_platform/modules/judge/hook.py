"""Generation-worker entry points. Failures are stored and do not stop the job."""

from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from education_platform.modules.generation.item_gate import GatedItem, JudgeAttempt
from education_platform.modules.generation.items import GeneratedItem
from education_platform.modules.judge.checks import ItemTarget, LessonTarget
from education_platform.modules.judge.evaluate import (
    Evaluation,
    apply_to_gate,
    evaluate_item,
    evaluate_lesson,
    evaluation_from_error,
    store_evaluation,
)
from education_platform.modules.judge.policy import (
    DECISION_ERROR,
    DECISION_REJECT,
    DECISION_REVIEW,
    judge_mode,
)
from education_platform.modules.judge.protocol import Judge

logger = logging.getLogger(__name__)


class ItemJudgeTracker:
    """Calls Jev on each gate attempt and keeps the latest evaluation.

    Enforce returns a reject so the writer retries. Shadow returns accept so
    the gate result is unchanged. An error or a raised failure returns None
    after logging, which the gate treats as no extra failure.
    """

    def __init__(
        self,
        judge: Judge,
        target_for: Callable[[GeneratedItem], ItemTarget],
        *,
        enforce: bool,
    ) -> None:
        self.evaluation: Evaluation | None = None
        self._judge = judge
        self._target_for = target_for
        self._enforce = enforce

    def __call__(self, item: GeneratedItem) -> JudgeAttempt | None:
        try:
            evaluation = evaluate_item(self._target_for(item), self._judge)
        except Exception:
            logger.warning("Jev item judge failed open", exc_info=True)
            return None
        self.evaluation = evaluation
        if evaluation.decision == DECISION_ERROR:
            logger.warning("Jev item judge unavailable: %s", evaluation.error)
            return JudgeAttempt(decision="error")
        if not self._enforce:
            return JudgeAttempt(decision="accept")
        if evaluation.decision == DECISION_REJECT or evaluation.gate_decision == DECISION_REJECT:
            reasons = evaluation.gate_reasons or evaluation.reasons
            return JudgeAttempt(decision="reject", reasons=reasons)
        if evaluation.decision == DECISION_REVIEW or evaluation.gate_decision == DECISION_REVIEW:
            return JudgeAttempt(decision="needs_review", needs_review=True)
        return JudgeAttempt(decision="accept")


def fold_item(
    gated: GatedItem,
    target: ItemTarget,
    judge: Judge | None,
    *,
    mode: str | None = None,
) -> tuple[GatedItem, Evaluation | None]:
    selected = mode if mode is not None else judge_mode()
    if judge is None or selected == "off":
        return gated, None
    try:
        evaluation = evaluate_item(target, judge)
    except Exception as exc:
        logger.warning("Jev item judge failed", exc_info=True)
        evaluation = evaluation_from_error(
            subject_kind="quiz_item",
            label=target.prompt,
            error=str(exc),
        )
    return apply_to_gate(gated, evaluation, mode=selected), evaluation


def fold_lesson(
    target: LessonTarget,
    judge: Judge | None,
    *,
    include_claims: bool,
) -> Evaluation | None:
    if judge is None or judge_mode() == "off":
        return None
    try:
        return evaluate_lesson(target, judge, include_claims=include_claims)
    except Exception as exc:
        logger.warning("Jev lesson judge failed", exc_info=True)
        return evaluation_from_error(
            subject_kind=target.kind,
            label=target.heading,
            error=str(exc),
        )


def remember(
    session: Session,
    evaluation: Evaluation | None,
    *,
    run_id: UUID,
    generation_job_id: UUID | None,
    subject_key: UUID | None,
    question_version_id: UUID | None,
    gate_verdict: str | None,
    gate_servable: bool | None,
    blind_status: str | None,
    blind_answer: str | None,
    mode: str | None = None,
) -> None:
    if evaluation is None:
        return
    store_evaluation(
        session,
        evaluation,
        run_id=run_id,
        generation_job_id=generation_job_id,
        subject_key=subject_key,
        question_version_id=question_version_id,
        mode=mode if mode is not None else judge_mode(),
        gate_verdict=gate_verdict,
        gate_servable=gate_servable,
        blind_status=blind_status,
        blind_answer=blind_answer,
    )


def blind_fields(raw: dict[str, object] | None) -> tuple[str | None, str | None]:
    if raw is None:
        return None, None
    status = raw.get("status")
    answer = raw.get("answer")
    return (
        status if isinstance(status, str) else None,
        answer if isinstance(answer, str) else None,
    )
