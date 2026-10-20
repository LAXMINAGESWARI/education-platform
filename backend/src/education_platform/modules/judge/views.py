"""Read-only Jev rows for the admin generation step panels."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.judge.models import JudgeEvaluation
from education_platform.modules.judge.policy import HOLD_CHECK_ID


@dataclass(frozen=True, slots=True)
class JudgeCheckView:
    check_id: str
    kind: str
    decision: str
    summary: str
    probabilities: dict[str, float]


@dataclass(frozen=True, slots=True)
class JudgeReadout:
    subject_kind: str
    subject_key: UUID | None
    label: str
    decision: str
    reasons: tuple[str, ...]
    checks: tuple[JudgeCheckView, ...]
    model: str | None
    question_version_id: UUID | None = None


async def load_judge_readouts(session: AsyncSession, run_id: UUID) -> tuple[JudgeReadout, ...]:
    rows = (
        await session.scalars(
            select(JudgeEvaluation)
            .where(JudgeEvaluation.run_id == run_id)
            .order_by(JudgeEvaluation.created_at)
        )
    ).all()
    latest: dict[tuple[str, str], JudgeEvaluation] = {}
    for row in rows:
        key = (row.subject_kind, str(row.subject_key or row.question_version_id or row.id))
        latest[key] = row
    return tuple(_readout(row) for row in latest.values())


def _readout(row: JudgeEvaluation) -> JudgeReadout:
    return JudgeReadout(
        subject_kind=row.subject_kind,
        subject_key=row.subject_key,
        label=row.label or row.subject_kind.replace("_", " "),
        decision=row.decision,
        reasons=tuple(str(item) for item in row.reasons if isinstance(item, str)),
        checks=tuple(_checks(row.checks)),
        model=row.model,
        question_version_id=row.question_version_id,
    )


def _checks(raw: object) -> list[JudgeCheckView]:
    if not isinstance(raw, list):
        return []
    views: list[JudgeCheckView] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        check_id = item.get("check_id")
        kind = item.get("kind")
        decision = item.get("decision")
        summary = item.get("summary")
        if not all(isinstance(value, str) for value in (check_id, kind, decision, summary)):
            continue
        if check_id == HOLD_CHECK_ID:
            continue
        probabilities: dict[str, float] = {}
        raw_probs = item.get("probabilities")
        if isinstance(raw_probs, dict):
            for key, value in raw_probs.items():
                if (
                    isinstance(key, str)
                    and isinstance(value, int | float)
                    and not isinstance(value, bool)
                ):
                    probabilities[key] = float(value)
        views.append(
            JudgeCheckView(
                check_id=str(check_id),
                kind=str(kind),
                decision=str(decision),
                summary=str(summary),
                probabilities=probabilities,
            )
        )
    return views
