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
from education_platform.modules.assessments.quiz_audit import (
    _keeper,
    audit_quiz_items,
    retire_flagged,
)


def _version_with_items(session: Session) -> tuple[UUID, list[QuizItem]]:
    version_id = session.scalar(select(QuizItem.quiz_version_id).limit(1))
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
    _version_id, items = _version_with_items(seeded_db)
    keeper = seeded_db.get(QuestionVersion, items[0].question_version_id)
    extra = seeded_db.get(QuestionVersion, items[1].question_version_id)
    assert keeper is not None and extra is not None
    extra.prompt = keeper.prompt
    seeded_db.commit()

    report = audit_quiz_items(seeded_db)
    grouped = {item.question_version_id for group in report.duplicate_groups for item in group}
    assert keeper.id in grouped
    assert extra.id in grouped
    shared = [
        group
        for group in report.duplicate_groups
        if {keeper.id, extra.id} <= {item.question_version_id for item in group}
    ]
    assert len(shared) == 1
    kept_id = _keeper(shared[0]).question_version_id
    invalid_ids = {item.question_version_id for item in report.invalid}
    # A duplicate keeper is archived only when that same version is also invalid.
    assert kept_id not in report.retire_ids or kept_id in invalid_ids
    changed = retire_flagged(seeded_db, report)
    seeded_db.commit()
    assert changed >= 1
    seeded_db.refresh(keeper)
    seeded_db.refresh(extra)
    for row in (keeper, extra):
        if row.id in report.retire_ids:
            assert row.lifecycle_status is QuestionVersionStatus.ARCHIVED
        else:
            assert row.lifecycle_status is QuestionVersionStatus.PUBLISHED
    still_linked = seeded_db.get(QuizItem, items[1].id)
    assert still_linked is not None
    assert still_linked.question_version_id == extra.id
