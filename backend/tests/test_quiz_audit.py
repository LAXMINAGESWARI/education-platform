"""Audit retirement archives versions and the draw skips them."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from education_platform.modules.assessments.models import (
    QuestionAnswerKey,
    QuestionVersion,
    QuestionVersionStatus,
    QuizItem,
)
from education_platform.modules.assessments.question_selection import _load_bank
from education_platform.modules.assessments.quiz_audit import audit_quiz_items, retire_flagged


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
    loaded = [seeded_db.get(QuestionVersion, item.question_version_id) for item in items[:2]]
    assert loaded[0] is not None and loaded[1] is not None
    a, b = loaded
    # _keeper keeps the published version with the lowest str(UUID), not items[1].
    keeper, extra = sorted((a, b), key=lambda version: str(version.id))
    # A seeded stem can match other questions, and an explanation that fails
    # item checks is archived even when that row wins _keeper. Isolate a valid pair.
    stem = "audit pair stem with no seeded neighbour"
    rationale = "The keyed option is the intended answer."
    for version in (keeper, extra):
        version.prompt = stem
        version.explanation = rationale
        answer_key = seeded_db.scalar(
            select(QuestionAnswerKey).where(QuestionAnswerKey.question_version_id == version.id)
        )
        if answer_key is not None:
            answer_key.correct_rationale = rationale
    seeded_db.commit()

    report = audit_quiz_items(seeded_db)
    grouped = {item.question_version_id for group in report.duplicate_groups for item in group}
    assert keeper.id in grouped
    assert extra.id in grouped
    changed = retire_flagged(seeded_db, report)
    seeded_db.commit()
    assert changed >= 1
    seeded_db.refresh(extra)
    seeded_db.refresh(keeper)
    assert extra.lifecycle_status is QuestionVersionStatus.ARCHIVED
    assert keeper.lifecycle_status is QuestionVersionStatus.PUBLISHED
    extra_item = next(item for item in items if item.question_version_id == extra.id)
    still_linked = seeded_db.get(QuizItem, extra_item.id)
    assert still_linked is not None
    assert still_linked.question_version_id == extra.id
