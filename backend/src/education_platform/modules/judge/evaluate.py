"""Run lesson and item checks and fold them into the existing item gate.

Shadow mode stores the policy and returns the gated item unchanged. Augment
drops an item Jev rejects on a check it owns. Replace lets Jev override a
blind-solve drop on non-computational items. Keyed-answer checks on
computational items stay with the solver in every mode.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from education_platform.core.config import Settings, get_settings
from education_platform.modules.generation.item_gate import GatedItem
from education_platform.modules.judge.checks import (
    ItemTarget,
    LessonTarget,
    item_plan,
    item_request,
    lesson_plan,
    lesson_request,
    looks_computational,
)
from education_platform.modules.judge.client import HttpJudge, estimate_cost_usd
from education_platform.modules.judge.errors import JudgeError
from education_platform.modules.judge.models import JudgeEvaluation
from education_platform.modules.judge.policy import (
    DECISION_ACCEPT,
    DECISION_ERROR,
    DECISION_REJECT,
    Thresholds,
    apply_policy,
    judge_mode,
)
from education_platform.modules.judge.protocol import Judge
from education_platform.modules.judge.types import CheckResult, DecisionRequest, PlannedCheck

logger = logging.getLogger(__name__)

_SOLVER_REASON = "blind solve"


@dataclass(frozen=True, slots=True)
class Evaluation:
    subject_kind: str
    label: str
    decision: str
    gate_decision: str
    reasons: tuple[str, ...]
    gate_reasons: tuple[str, ...]
    checks: tuple[CheckResult, ...]
    model: str | None
    provider: str | None
    request_id: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    error: str | None
    computational: bool
    subject_key: UUID | None = None


def open_judge(settings: Settings | None = None) -> Judge | None:
    """Live client when this mode should call Jev. Missing credentials skip the call."""
    cfg = settings or get_settings()
    if judge_mode(cfg) == "off":
        return None
    if not cfg.jev_base_url.strip() and not cfg.openrouter_configured:
        return None
    return HttpJudge(cfg)


def evaluate_lesson(
    target: LessonTarget,
    judge: Judge,
    *,
    include_claims: bool,
    settings: Settings | None = None,
) -> Evaluation:
    plan = lesson_plan(target, include_claims=include_claims)
    return _evaluate(
        subject_kind=target.kind,
        label=target.heading,
        plan=plan,
        request_state=lesson_request(target, plan),
        judge=judge,
        computational=False,
        settings=settings,
    )


def evaluate_item(
    target: ItemTarget,
    judge: Judge,
    *,
    settings: Settings | None = None,
) -> Evaluation:
    plan = item_plan(target)
    computational = looks_computational(
        target.prompt,
        target.correct_rationale,
        tuple(target.options.values()),
    )
    return _evaluate(
        subject_kind="quiz_item",
        label=target.prompt,
        plan=plan,
        request_state=item_request(target, plan),
        judge=judge,
        computational=computational,
        settings=settings,
    )


def evaluation_from_error(
    *,
    subject_kind: str,
    label: str,
    error: str,
    computational: bool = False,
) -> Evaluation:
    return Evaluation(
        subject_kind=subject_kind,
        label=label,
        decision=DECISION_ERROR,
        gate_decision=DECISION_ACCEPT,
        reasons=(error,),
        gate_reasons=(),
        checks=(),
        model=None,
        provider=None,
        request_id=None,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=0,
        error=error,
        computational=computational,
    )


def apply_to_gate(
    gated: GatedItem,
    evaluation: Evaluation | None,
    *,
    mode: str,
) -> GatedItem:
    """Shadow and errors leave the gate result alone."""
    if evaluation is None or mode in {"off", "shadow"} or evaluation.decision == DECISION_ERROR:
        return gated
    if mode == "augment":
        return _augment(gated, evaluation)
    if mode == "replace":
        return _replace(gated, evaluation)
    return gated


def store_evaluation(
    session: Session,
    evaluation: Evaluation,
    *,
    run_id: UUID | None,
    generation_job_id: UUID | None,
    subject_key: UUID | None,
    question_version_id: UUID | None,
    mode: str,
    gate_verdict: str | None,
    gate_servable: bool | None,
    blind_status: str | None,
    blind_answer: str | None,
) -> None:
    session.add(
        JudgeEvaluation(
            run_id=run_id,
            generation_job_id=generation_job_id,
            question_version_id=question_version_id,
            subject_kind=evaluation.subject_kind,
            subject_key=subject_key,
            label=evaluation.label[:200],
            mode=mode if mode in {"off", "shadow", "augment", "replace"} else "shadow",
            decision=evaluation.decision
            if evaluation.decision in {"accept", "needs_review", "reject", "error"}
            else "error",
            reasons=list(evaluation.reasons),
            checks=[check.to_json() for check in evaluation.checks],
            model=evaluation.model,
            provider=evaluation.provider,
            request_id=evaluation.request_id,
            input_tokens=evaluation.input_tokens,
            output_tokens=evaluation.output_tokens,
            cost_usd=Decimal(f"{max(evaluation.cost_usd, 0.0):.8f}"),
            latency_ms=evaluation.latency_ms,
            error=evaluation.error,
            blind_status=blind_status,
            blind_answer=blind_answer,
            gate_verdict=gate_verdict,
            gate_servable=gate_servable,
        )
    )


def _evaluate(
    *,
    subject_kind: str,
    label: str,
    plan: Sequence[PlannedCheck],
    request_state: DecisionRequest,
    judge: Judge,
    computational: bool,
    settings: Settings | None,
) -> Evaluation:
    try:
        result = judge.decide(request_state)
    except JudgeError as exc:
        return evaluation_from_error(
            subject_kind=subject_kind,
            label=label,
            error=str(exc),
            computational=computational,
        )
    outcome = apply_policy(
        plan,
        dict(result.answers),
        computational=computational,
        thresholds=Thresholds.from_settings(settings),
    )
    cost = (
        result.cost_usd if result.cost_usd is not None else estimate_cost_usd(result.input_tokens)
    )
    return Evaluation(
        subject_kind=subject_kind,
        label=label,
        decision=outcome.decision,
        gate_decision=outcome.gate_decision,
        reasons=outcome.reasons,
        gate_reasons=outcome.gate_reasons,
        checks=outcome.checks,
        model=result.model or None,
        provider=result.provider,
        request_id=result.request_id,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=cost,
        latency_ms=result.latency_ms,
        error=None,
        computational=computational,
    )


def _augment(gated: GatedItem, evaluation: Evaluation) -> GatedItem:
    if evaluation.gate_decision != DECISION_REJECT or not gated.servable:
        return gated
    return replace(
        gated,
        verdict="dropped",
        servable=False,
        reasons=gated.reasons + evaluation.gate_reasons,
    )


def _replace(gated: GatedItem, evaluation: Evaluation) -> GatedItem:
    structural = tuple(reason for reason in gated.reasons if not reason.startswith(_SOLVER_REASON))
    if evaluation.computational:
        if evaluation.gate_decision == DECISION_REJECT and gated.servable:
            return replace(
                gated,
                verdict="dropped",
                servable=False,
                reasons=gated.reasons + evaluation.gate_reasons,
            )
        return gated
    if structural and not gated.servable:
        return gated
    if evaluation.gate_decision == DECISION_ACCEPT and not gated.servable and not structural:
        verdict = "passed" if gated.attempts == 1 else "fixed_on_retry"
        return replace(gated, verdict=verdict, servable=True, reasons=())
    if evaluation.gate_decision == DECISION_REJECT:
        return replace(
            gated,
            verdict="dropped",
            servable=False,
            reasons=structural + evaluation.gate_reasons,
        )
    return gated
