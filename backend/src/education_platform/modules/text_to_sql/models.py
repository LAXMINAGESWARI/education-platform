"""One row per streamed ask, shared by every API pod."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from education_platform.db.base import Base, UUIDPrimaryKeyMixin
from education_platform.db.types import JsonDict


class TextToSqlAskRun(UUIDPrimaryKeyMixin, Base):
    """Terminal frames and the progress snapshot for one (user, request_id)."""

    __tablename__ = "text_to_sql_ask_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_text_to_sql_ask_runs_user_request"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    progress: Mapped[list[Any]] = mapped_column(JsonDict, nullable=False)
    terminal: Mapped[list[Any]] = mapped_column(JsonDict, nullable=False)
    sql_executed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    audited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
