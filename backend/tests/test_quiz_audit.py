"""Audit retirement archives versions and the draw skips them."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from education_platform.modules.assessments.models import (
    QuestionVersion,
    QuestionVersionStatus,
    QuizItem,
)
from education_platform.modules.assessments.question_selection import _load_bank
from education_platform.modules.assessments.quiz_audit import audit_quiz_items, retire_flagged


def _version_with_items(session: Session) -> tuple[UUID, list[QuizItem]]:
    version_id = session.scalar(
        select(QuizItem.quiz_version_id)
        .order_by(QuizItem.quiz_version_id, QuizItem.sequence, QuizItem.id)
        .limit(1)
    )
    assert version_id is not None
    items = list(
        session.scalars(
            select(QuizItem)
            .where(QuizItem.quiz_version_id == version_id)
            .order_by(QuizItem.sequence)
        ).all()
    )
    assert len(items) >= 2
    return version_id, items


def _clean_pair(session: Session) -> tuple[QuizItem, QuizItem]:
    """Two items of one quiz version whose question versions the audit leaves alone.

    Seeded data includes versions the audit already flags (invalid or duplicate);
    those are archived regardless, so neither side of the pair may be one of them.
    """
    baseline = audit_quiz_items(session)
    flagged = {item.question_version_id for item in baseline.invalid}
    flagged |= {item.question_version_id for group in baseline.duplicate_groups for item in group}
    items = session.scalars(
        select(QuizItem).order_by(QuizItem.quiz_version_id, QuizItem.sequence, QuizItem.id)
    ).all()
    by_version: dict[UUID, list[QuizItem]] = {}
    for item in items:
        if item.question_version_id not in flagged:
            by_version.setdefault(item.quiz_version_id, []).append(item)
    for clean in by_version.values():
        if len(clean) >= 2:
            return clean[0], clean[1]
    pytest.fail("seed has no quiz version with two unflagged question versions")


@pytest.mark.asyncio
async def test_load_bank_skips_archived_question_versions(
    seeded_db: Session, async_db_session: AsyncSession
) -> None:
    version_id, items = _version_with_items(seeded_db)
    archived = seeded_db.get(QuestionVersion, items[0].question_version_id)
    assert archived is not None
    archived.lifecycle_status = QuestionVersionStatus.ARCHIVED
    seeded_db.commit()

    bank = await _load_bank(async_db_session, version_id)
    served = {question.question_version_id for question in bank}
    assert archived.id not in served
    assert len(bank) == len(items) - 1


def test_retire_archives_duplicate_and_keeps_the_quiz_row(seeded_db: Session) -> None:
    pair = _clean_pair(seeded_db)
    first = seeded_db.get(QuestionVersion, pair[0].question_version_id)
    second = seeded_db.get(QuestionVersion, pair[1].question_version_id)
    assert first is not None and second is not None
    assert first.lifecycle_status is QuestionVersionStatus.PUBLISHED
    assert second.lifecycle_status is QuestionVersionStatus.PUBLISHED
    second.prompt = first.prompt
    seeded_db.commit()

    # _keeper() keeps the published version with the lowest str(UUID); mirror that.
    keeper, extra = sorted((first, second), key=lambda v: str(v.id))
    extra_item = next(item for item in pair if item.question_version_id == extra.id)

    report = audit_quiz_items(seeded_db)
    grouped = {item.question_version_id for group in report.duplicate_groups for item in group}
    assert keeper.id in grouped
    assert extra.id in grouped
    changed = retire_flagged(seeded_db, report)
    seeded_db.commit()
    assert changed >= 1
    seeded_db.refresh(keeper)
    seeded_db.refresh(extra)
    assert extra.lifecycle_status is QuestionVersionStatus.ARCHIVED
    assert keeper.lifecycle_status is QuestionVersionStatus.PUBLISHED
    still_linked = seeded_db.get(QuizItem, extra_item.id)
    assert still_linked is not None
    assert still_linked.question_version_id == extra.id
