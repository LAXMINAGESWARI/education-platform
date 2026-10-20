"""Append-only audit events — see `docs/design/02` section 10."""

from __future__ import annotations

import enum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.audit.models import AuditEvent


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
