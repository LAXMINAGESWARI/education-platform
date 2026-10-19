"""Find invalid or duplicate quiz items and archive them.

Archiving sets ``question_versions.lifecycle_status``. Quiz rows, attempt rows,
and the version itself stay in place. The draw skips archived versions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.modules.assessments.models import (
    QuestionAnswerKey,
    QuestionOption,
    QuestionVersion,
    QuestionVersionStatus,
)
from education_platform.modules.assessments.prompt_identity import same_stem
from education_platform.modules.generation.item_checks import numeric_problems, structural_problems

_LABELS = ("A", "B", "C", "D")


@dataclass(frozen=True, slots=True)
class AuditedItem:
    question_version_id: UUID
    prompt: str
    status: QuestionVersionStatus
    reasons: tuple[str, ...]


@dataclass
class AuditReport:
    invalid: list[AuditedItem] = field(default_factory=list)
    duplicate_groups: list[list[AuditedItem]] = field(default_factory=list)

    @property
    def retire_ids(self) -> set[UUID]:
        chosen: set[UUID] = set()
        for item in self.invalid:
            chosen.add(item.question_version_id)
        for group in self.duplicate_groups:
            keeper = _keeper(group)
            for item in group:
                if item.question_version_id != keeper.question_version_id:
                    chosen.add(item.question_version_id)
        return chosen


def audit_quiz_items(session: Session) -> AuditReport:
    versions = list(session.scalars(select(QuestionVersion)).all())
    report = AuditReport()
    live: list[AuditedItem] = []
    for version in versions:
        if version.lifecycle_status is QuestionVersionStatus.ARCHIVED:
            continue
        options = {
            row.label: row.text
            for row in session.scalars(
                select(QuestionOption).where(QuestionOption.question_version_id == version.id)
            ).all()
        }
        key = session.scalar(
            select(QuestionAnswerKey).where(QuestionAnswerKey.question_version_id == version.id)
        )
        label = key.correct_option_label if key is not None and key.correct_option_label else ""
        explanation = ""
        if key is not None and key.correct_rationale:
            explanation = key.correct_rationale
        elif version.explanation:
            explanation = version.explanation
        present = {name: options.get(name, "") for name in _LABELS}
        reasons = list(structural_problems(present, label, explanation))
        reasons.extend(numeric_problems(present, label, explanation))
        item = AuditedItem(
            question_version_id=version.id,
            prompt=version.prompt,
            status=version.lifecycle_status,
            reasons=tuple(reasons),
        )
        if reasons:
            report.invalid.append(item)
        live.append(item)
    report.duplicate_groups = _duplicate_groups(live)
    return report


def retire_flagged(session: Session, report: AuditReport) -> int:
    """Archive flagged versions. Does not delete quiz items or attempt history."""
    ids = report.retire_ids
    if not ids:
        return 0
    versions = session.scalars(
        select(QuestionVersion).where(QuestionVersion.id.in_(tuple(ids)))
    ).all()
    changed = 0
    for version in versions:
        if version.lifecycle_status is QuestionVersionStatus.ARCHIVED:
            continue
        version.lifecycle_status = QuestionVersionStatus.ARCHIVED
        changed += 1
    session.flush()
    return changed


def _duplicate_groups(items: list[AuditedItem]) -> list[list[AuditedItem]]:
    groups: list[list[AuditedItem]] = []
    used: set[UUID] = set()
    for item in items:
        if item.question_version_id in used:
            continue
        cluster = [item]
        used.add(item.question_version_id)
        for other in items:
            if other.question_version_id in used:
                continue
            if same_stem(item.prompt, other.prompt):
                cluster.append(other)
                used.add(other.question_version_id)
        if len(cluster) > 1:
            groups.append(cluster)
    return groups


def _keeper(group: list[AuditedItem]) -> AuditedItem:
    def rank(item: AuditedItem) -> tuple[int, str]:
        published = 0 if item.status is QuestionVersionStatus.PUBLISHED else 1
        return (published, str(item.question_version_id))

    return min(group, key=rank)
