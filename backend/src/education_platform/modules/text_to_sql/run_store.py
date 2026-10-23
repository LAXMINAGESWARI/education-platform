"""Shared ask-run store. A reconnect reads this instead of executing SQL again.

The database store is what API pods use. Tests can pass the memory store.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from education_platform.core.config import get_settings
from education_platform.db.session import get_session_factory
from education_platform.modules.text_to_sql.models import TextToSqlAskRun

_active_store: ContextVar[RunStore | None] = ContextVar("text_to_sql_run_store", default=None)


def active_store() -> RunStore | None:
    return _active_store.get()


def push_store(store: RunStore) -> Any:
    return _active_store.set(store)


def reset_store(token: Any) -> None:
    _active_store.reset(token)


@dataclass
class AskRunView:
    status: str
    progress: list[dict[str, Any]]
    terminal: list[dict[str, Any]]
    sql_executed: bool
    expired: bool


class RunStore(Protocol):
    async def claim(self, user_id: str, request_id: str) -> bool: ...

    async def load(self, user_id: str, request_id: str) -> AskRunView | None: ...

    async def append_progress(
        self, user_id: str, request_id: str, payload: dict[str, Any]
    ) -> None: ...

    async def append_terminal(
        self, user_id: str, request_id: str, event: str, payload: dict[str, Any], *, close: bool
    ) -> None: ...

    async def mark_sql_executed(self, user_id: str, request_id: str) -> None: ...


def _view(
    status: str,
    progress: list[Any],
    terminal: list[Any],
    sql_executed: bool,
    expires_at: datetime,
    now: datetime,
) -> AskRunView:
    return AskRunView(
        status=status,
        progress=list(progress),
        terminal=list(terminal),
        sql_executed=sql_executed,
        expired=expires_at <= now,
    )


@dataclass
class _MemoryRow:
    status: str
    progress: list[dict[str, Any]] = field(default_factory=list)
    terminal: list[dict[str, Any]] = field(default_factory=list)
    sql_executed: bool = False
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class MemoryRunStore:
    """Process-local store. The database store is the one pods share."""

    def __init__(
        self, *, ttl_seconds: float = 600, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._rows: dict[tuple[str, str], _MemoryRow] = {}
        self._lock = asyncio.Lock()

    def _now(self) -> datetime:
        return self._clock()

    async def claim(self, user_id: str, request_id: str) -> bool:
        async with self._lock:
            key = (user_id, request_id)
            now = self._now()
            row = self._rows.get(key)
            if row is None or row.expires_at <= now:
                self._rows[key] = _MemoryRow(
                    status="running",
                    expires_at=now + timedelta(seconds=self._ttl),
                )
                return True
            return False

    async def load(self, user_id: str, request_id: str) -> AskRunView | None:
        async with self._lock:
            row = self._rows.get((user_id, request_id))
            if row is None:
                return None
            return _view(
                row.status,
                row.progress,
                row.terminal,
                row.sql_executed,
                row.expires_at,
                self._now(),
            )

    async def append_progress(self, user_id: str, request_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            row = self._rows[(user_id, request_id)]
            row.progress.append(payload)

    async def append_terminal(
        self, user_id: str, request_id: str, event: str, payload: dict[str, Any], *, close: bool
    ) -> None:
        async with self._lock:
            row = self._rows[(user_id, request_id)]
            row.terminal.append({"event": event, "data": payload})
            if close:
                row.status = "done"

    async def mark_sql_executed(self, user_id: str, request_id: str) -> None:
        async with self._lock:
            row = self._rows.get((user_id, request_id))
            if row is not None:
                row.sql_executed = True


class DbRunStore:
    """Postgres row keyed by (user_id, request_id). Visible to every API pod."""

    def __init__(self, *, ttl_seconds: float | None = None) -> None:
        self._ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else get_settings().text_to_sql_stream_ttl_seconds
        )

    async def claim(self, user_id: str, request_id: str) -> bool:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self._ttl)
        user = UUID(user_id)
        request = UUID(request_id)
        factory = get_session_factory()
        async with factory() as session:
            inserted = await session.execute(
                pg_insert(TextToSqlAskRun)
                .values(
                    user_id=user,
                    request_id=request,
                    status="running",
                    progress=[],
                    terminal=[],
                    sql_executed=False,
                    audited=False,
                    expires_at=expires,
                    updated_at=now,
                )
                .on_conflict_do_nothing(constraint="uq_text_to_sql_ask_runs_user_request")
                .returning(TextToSqlAskRun.id)
            )
            if inserted.first() is not None:
                await session.commit()
                return True
            taken = await session.execute(
                update(TextToSqlAskRun)
                .where(
                    TextToSqlAskRun.user_id == user,
                    TextToSqlAskRun.request_id == request,
                    TextToSqlAskRun.expires_at <= now,
                )
                .values(
                    status="running",
                    progress=[],
                    terminal=[],
                    sql_executed=False,
                    audited=False,
                    expires_at=expires,
                    updated_at=now,
                )
                .returning(TextToSqlAskRun.id)
            )
            won = taken.first() is not None
            await session.commit()
            return won

    async def load(self, user_id: str, request_id: str) -> AskRunView | None:
        factory = get_session_factory()
        async with factory() as session:
            found = await _find(session, user_id, request_id)
            if found is None:
                return None
            return _view(
                found.status,
                found.progress,
                found.terminal,
                found.sql_executed,
                found.expires_at,
                datetime.now(UTC),
            )

    async def append_progress(self, user_id: str, request_id: str, payload: dict[str, Any]) -> None:
        await self._mutate(user_id, request_id, progress_item=payload)

    async def append_terminal(
        self, user_id: str, request_id: str, event: str, payload: dict[str, Any], *, close: bool
    ) -> None:
        await self._mutate(
            user_id, request_id, terminal_item={"event": event, "data": payload}, close=close
        )

    async def mark_sql_executed(self, user_id: str, request_id: str) -> None:
        factory = get_session_factory()
        async with factory() as session:
            found = await _find(session, user_id, request_id)
            if found is None:
                return
            found.sql_executed = True
            found.updated_at = datetime.now(UTC)
            await session.commit()

    async def _mutate(
        self,
        user_id: str,
        request_id: str,
        *,
        progress_item: dict[str, Any] | None = None,
        terminal_item: dict[str, Any] | None = None,
        close: bool = False,
    ) -> None:
        factory = get_session_factory()
        async with factory() as session:
            found = await _find(session, user_id, request_id)
            if found is None:
                return
            if progress_item is not None:
                found.progress = [*list(found.progress), progress_item]
            if terminal_item is not None:
                found.terminal = [*list(found.terminal), terminal_item]
            if close:
                found.status = "done"
            found.updated_at = datetime.now(UTC)
            await session.commit()


async def _find(session: Any, user_id: str, request_id: str) -> TextToSqlAskRun | None:
    result = await session.execute(
        select(TextToSqlAskRun).where(
            TextToSqlAskRun.user_id == UUID(user_id),
            TextToSqlAskRun.request_id == UUID(request_id),
        )
    )
    found = result.scalar_one_or_none()
    if not isinstance(found, TextToSqlAskRun):
        return None
    return found
