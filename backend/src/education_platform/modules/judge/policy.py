"""Turn Jev probabilities into accept, needs review, or reject.

Thresholds come from settings. Every rejecting check drives the gate. The
blind solver still runs as well: an item is kept only when both pass.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from education_platform.core.config import Settings, get_settings
from education_platform.modules.judge.types import (
    Answer,
    CheckResult,
    ChoiceAnswer,
    NoulAnswer,
    PlannedCheck,
    ScoreAnswer,
)

DECISION_ACCEPT = "accept"
DECISION_REVIEW = "needs_review"
DECISION_REJECT = "reject"
DECISION_ERROR = "error"
_RANK = {DECISION_ACCEPT: 0, DECISION_REVIEW: 1, DECISION_REJECT: 2, DECISION_ERROR: 3}


@dataclass(frozen=True, slots=True)
class Thresholds:
    noul_accept: float = 0.80
    noul_reject: float = 0.35
    confidence_accept: float = 0.55
    confidence_reject: float = 0.70
    coverage_accept_score: float = 2.0
    coverage_reject_score: float = 0.75

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Thresholds:
        cfg = settings or get_settings()
        return cls(
            noul_accept=cfg.jev_noul_accept,
            noul_reject=cfg.jev_noul_reject,
            confidence_accept=cfg.jev_confidence_accept,
            confidence_reject=cfg.jev_confidence_reject,
            coverage_accept_score=cfg.jev_coverage_accept_score,
            coverage_reject_score=cfg.jev_coverage_reject_score,
        )


@dataclass(frozen=True, slots=True)
class PolicyOutcome:
    decision: str
    gate_decision: str
    reasons: tuple[str, ...]
    gate_reasons: tuple[str, ...]
    checks: tuple[CheckResult, ...]


def judge_mode(settings: Settings | None = None) -> str:
    value = (settings or get_settings()).jev_judge_mode.strip().lower()
    if value in {"off", "shadow", "enforce"}:
        return value
    return "enforce"


def apply_policy(
    plan: Sequence[PlannedCheck],
    answers: dict[str, Answer],
    *,
    thresholds: Thresholds | None = None,
) -> PolicyOutcome:
    limits = thresholds or Thresholds.from_settings()
    checks: list[CheckResult] = []
    for planned in plan:
        answer = answers.get(planned.check_id)
        if answer is None:
            checks.append(_missing(planned))
            continue
        scored = _score_check(planned, answer, limits)
        checks.append(
            CheckResult(
                check_id=scored.check_id,
                kind=scored.kind,
                decision=scored.decision,
                summary=scored.summary,
                reasons=scored.reasons,
                probabilities=scored.probabilities,
                drives_gate=True,
                noul=scored.noul,
                choice=scored.choice,
                score=scored.score,
                confidence=scored.confidence,
                legend=scored.legend,
            )
        )
    return _collapse(tuple(checks))


def _score_check(planned: PlannedCheck, answer: Answer, limits: Thresholds) -> CheckResult:
    if planned.rule == "noul":
        return _noul(planned, answer, limits)
    if planned.rule == "coverage":
        return _coverage(planned, answer, limits)
    if planned.rule == "grade":
        return _grade(planned, answer)
    if planned.rule == "difficulty":
        return _difficulty(planned, answer, limits)
    return _missing(planned)


def _noul(planned: PlannedCheck, answer: Answer, limits: Thresholds) -> CheckResult:
    if not isinstance(answer, NoulAnswer):
        return _wrong_type(planned, "noul")
    probability = answer.noul
    if probability >= limits.noul_accept:
        decision = DECISION_ACCEPT
        reasons: tuple[str, ...] = ()
    elif probability <= limits.noul_reject:
        decision = DECISION_REJECT
        reasons = (
            f"{planned.label}: noul {probability:.2f} is at or below {limits.noul_reject:.2f}.",
        )
    else:
        decision = DECISION_REVIEW
        reasons = (
            f"{planned.label}: noul {probability:.2f} is between "
            f"{limits.noul_reject:.2f} and {limits.noul_accept:.2f}.",
        )
    return CheckResult(
        check_id=planned.check_id,
        kind="noul",
        decision=decision,
        summary=f"{probability:.2f}",
        reasons=reasons,
        probabilities={"true": probability, "false": round(1.0 - probability, 6)},
        drives_gate=True,
        noul=probability,
    )


def _coverage(planned: PlannedCheck, answer: Answer, limits: Thresholds) -> CheckResult:
    if not isinstance(answer, ScoreAnswer):
        return _wrong_type(planned, "score")
    probs = answer.probabilities or {}
    none_mass = probs.get("0", 0.0)
    covered = probs.get("2", 0.0) + probs.get("3", 0.0)
    if probs and none_mass >= 0.60:
        decision = DECISION_REJECT
    elif (probs and covered >= 0.70) or answer.score >= limits.coverage_accept_score:
        decision = DECISION_ACCEPT
    elif answer.score <= limits.coverage_reject_score:
        decision = DECISION_REJECT
    else:
        decision = DECISION_REVIEW
    level = _level_name(answer)
    reasons: tuple[str, ...] = ()
    if decision != DECISION_ACCEPT:
        reasons = (f"Coverage of {planned.label!r} is {level} (score {answer.score:.2f}).",)
    return CheckResult(
        check_id=planned.check_id,
        kind="score",
        decision=decision,
        summary=f"{level} ({answer.score:.2f})",
        reasons=reasons,
        probabilities=dict(probs),
        drives_gate=True,
        score=answer.score,
        confidence=answer.confidence,
        legend=answer.legend,
    )


def _grade(planned: PlannedCheck, answer: Answer) -> CheckResult:
    if not isinstance(answer, ScoreAnswer):
        return _wrong_type(planned, "score")
    probs = answer.probabilities or {}
    on_grade = probs.get("1", 0.0)
    below = probs.get("0", 0.0)
    above = probs.get("2", 0.0)
    if probs and on_grade >= 0.55:
        decision = DECISION_ACCEPT
    elif probs and below >= 0.60:
        decision = DECISION_REJECT
    elif probs and above >= 0.70:
        decision = DECISION_REVIEW
    elif not probs and 0.5 <= answer.score <= 1.5:
        decision = DECISION_ACCEPT
    elif not probs and answer.score < 0.5:
        decision = DECISION_REJECT
    else:
        decision = DECISION_REVIEW
    level = _level_name(answer)
    reasons: tuple[str, ...] = ()
    if decision != DECISION_ACCEPT:
        reasons = (f"Grade level is {level} (score {answer.score:.2f}).",)
    return CheckResult(
        check_id=planned.check_id,
        kind="score",
        decision=decision,
        summary=f"{level} ({answer.score:.2f})",
        reasons=reasons,
        probabilities=dict(probs),
        drives_gate=True,
        score=answer.score,
        confidence=answer.confidence,
        legend=answer.legend,
    )


def _difficulty(planned: PlannedCheck, answer: Answer, limits: Thresholds) -> CheckResult:
    if not isinstance(answer, ChoiceAnswer):
        return _wrong_type(planned, "choice")
    stated = planned.stated or ""
    confidence = answer.confidence
    probs = dict(answer.probabilities or {})
    matches = answer.choice == stated
    if matches and (confidence is None or confidence >= limits.confidence_accept):
        decision = DECISION_ACCEPT
    elif not matches and confidence is not None and confidence >= limits.confidence_reject:
        decision = DECISION_REJECT
    else:
        decision = DECISION_REVIEW
    summary = answer.choice if confidence is None else f"{answer.choice} ({confidence:.2f})"
    reasons: tuple[str, ...] = ()
    if decision != DECISION_ACCEPT:
        confidence_note = "" if confidence is None else f" with confidence {confidence:.2f}"
        reasons = (f"Stated difficulty {stated}; Jev chose {answer.choice}{confidence_note}.",)
    return CheckResult(
        check_id=planned.check_id,
        kind="choice",
        decision=decision,
        summary=summary,
        reasons=reasons,
        probabilities=probs,
        drives_gate=True,
        choice=answer.choice,
        confidence=confidence,
    )


def _missing(planned: PlannedCheck) -> CheckResult:
    return CheckResult(
        check_id=planned.check_id,
        kind=planned.rule,
        decision=DECISION_ERROR,
        summary="missing",
        reasons=(f"{planned.label}: Jev did not return this check.",),
        probabilities={},
        drives_gate=False,
    )


def _wrong_type(planned: PlannedCheck, expected: str) -> CheckResult:
    return CheckResult(
        check_id=planned.check_id,
        kind=expected,
        decision=DECISION_ERROR,
        summary="unexpected type",
        reasons=(f"{planned.label}: expected a {expected} answer.",),
        probabilities={},
        drives_gate=False,
    )


def _level_name(answer: ScoreAnswer) -> str:
    if answer.legend:
        index = str(int(round(answer.score)))
        named = answer.legend.get(index)
        if isinstance(named, str) and named:
            return named
    return f"{answer.score:.2f}"


def _collapse(checks: tuple[CheckResult, ...]) -> PolicyOutcome:
    decision = _worst(check.decision for check in checks)
    driving = [check for check in checks if check.drives_gate]
    gate_decision = _worst(check.decision for check in driving) if driving else DECISION_ACCEPT
    reasons = tuple(reason for check in checks for reason in check.reasons)
    gate_reasons = tuple(reason for check in driving for reason in check.reasons)
    return PolicyOutcome(
        decision=decision,
        gate_decision=gate_decision,
        reasons=reasons,
        gate_reasons=gate_reasons,
        checks=checks,
    )


def _worst(decisions: Iterable[str]) -> str:
    chosen = DECISION_ACCEPT
    for decision in decisions:
        if _RANK.get(decision, 0) > _RANK.get(chosen, 0):
            chosen = decision
    return chosen
