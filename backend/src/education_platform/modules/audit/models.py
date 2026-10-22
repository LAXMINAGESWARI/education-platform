"""Audit event ORM — table name ``audit_events`` is unchanged."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from education_platform.db.base import Base, UUIDPrimaryKeyMixin
from education_platform.db.types import JsonDict


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_events"

    institution_id: Mapped[UUID] = mapped_column(ForeignKey("institutions.id"), index=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[UUID | None] = mapped_column(nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    # Set only for progress subscribes. One key per principal, subject, and
    # 10-minute bucket. Null for every other audit row.
    dedupe_key: Mapped[str | None] = mapped_column(String(400), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
