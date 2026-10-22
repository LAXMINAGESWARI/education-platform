"""Append-only audit events — see `docs/design/02` section 10."""

from __future__ import annotations

import enum
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.audit.models import AuditEvent

SUBSCRIBE_AUDIT_WINDOW = timedelta(minutes=10)


class AuditAction(str, enum.Enum):
    LOGIN = "auth.login"
    LOGIN_FAILED = "auth.login_failed"
    SCOPED_READ = "data.scoped_read"
    ASK_DATA = "data.ask"
    ASK_DOCUMENTS = "documents.ask"
    ASSISTANT_REFUSED = "assistant.refused"
    VIEW_FLAGS = "risk.view_flags"
    RECORD_INTERVENTION = "risk.record_intervention"
    QUIZ_APPROVED = "assessment.quiz_approved"
    QUIZ_SUBMITTED = "assessment.quiz_submitted"
    DOCUMENT_UPLOADED = "documents.uploaded"
    ATTENDANCE_RECORDED = "attendance.recorded"
    PERMISSION_DENIED = "auth.permission_denied"


async def record_event(
    session: AsyncSession,
    *,
    institution_id: UUID,
    event_type: AuditAction | str,
    entity_type: str,
    actor_user_id: UUID | None = None,
    entity_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
    flush: bool = True,
) -> AuditEvent:
    event = AuditEvent(
        institution_id=institution_id,
        actor_user_id=actor_user_id,
        event_type=event_type.value if isinstance(event_type, AuditAction) else str(event_type),
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload or {},
    )
    session.add(event)
    if flush:
        await session.flush()
    return event


def progress_subscribe_dedupe_key(
    *,
    institution_id: UUID,
    actor_user_id: UUID | None,
    entity_type: str,
    detail: str,
    now: datetime,
    window: timedelta = SUBSCRIBE_AUDIT_WINDOW,
) -> str:
    """Bucket a progress subscribe so one window can hold only one audit row."""
    seconds = int(window.total_seconds())
    if seconds <= 0:
        raise ValueError("window must be positive")
    bucket = int(now.timestamp()) // seconds
    actor = "none" if actor_user_id is None else str(actor_user_id)
    return f"{institution_id}:{actor}:{entity_type}:{detail}:{bucket}"


async def record_event_within_window(
    session: AsyncSession,
    *,
    window: timedelta,
    institution_id: UUID,
    event_type: AuditAction | str,
    entity_type: str,
    match_payload: dict[str, str],
    actor_user_id: UUID | None = None,
    entity_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent | None:
    """Insert one subscribe audit per dedupe key. A conflict inserts nothing.

    The unique ``dedupe_key`` makes concurrent subscribes race-proof. A later
    window uses a new key, so the next subscribe is audited again.
    """
    event_name = event_type.value if isinstance(event_type, AuditAction) else str(event_type)
    moment = datetime.now(UTC)
    key = progress_subscribe_dedupe_key(
        institution_id=institution_id,
        actor_user_id=actor_user_id,
        entity_type=entity_type,
        detail=match_payload.get("detail", ""),
        now=moment,
        window=window,
    )
    event_id = uuid4()
    stmt = (
        pg_insert(AuditEvent)
        .values(
            id=event_id,
            institution_id=institution_id,
            actor_user_id=actor_user_id,
            event_type=event_name,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload or {},
            dedupe_key=key,
            created_at=moment,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(AuditEvent.id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    if inserted is None:
        return None
    return await session.get(AuditEvent, inserted)


async def list_events(
    session: AsyncSession,
    *,
    institution_id: UUID,
    limit: int = 100,
    event_type: str | None = None,
) -> list[AuditEvent]:
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.institution_id == institution_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(min(limit, 500))
    )
    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    return list(await session.scalars(stmt))
