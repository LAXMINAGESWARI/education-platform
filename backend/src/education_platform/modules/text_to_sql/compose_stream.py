"""Optional compose hooks for the text-to-SQL progress stream.

The lean runner installs these only after ``execute_sql``, the audit write, and
``sanity_check`` have succeeded, so a table or answer delta cannot precede those
gates.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Protocol


class ComposeStream(Protocol):
    async def table(self, columns: list[str], rows: list[list[Any]]) -> None: ...

    async def delta(self, text: str) -> None: ...


class AskEvents(ComposeStream, Protocol):
    """Progress frames plus the compose hook. Table and delta stay behind the gates."""

    async def progress(self, stage: str, status: str) -> None: ...

    async def refusal(self, message: str) -> None: ...

    async def error(self, message: str) -> None: ...

    async def final(self, natural_answer: str, confidence: str | None) -> None: ...


_HOOKS: ContextVar[ComposeStream | None] = ContextVar("text_to_sql_compose_stream", default=None)


def push_compose_stream(hook: ComposeStream) -> Token[ComposeStream | None]:
    return _HOOKS.set(hook)


def reset_compose_stream(token: Token[ComposeStream | None]) -> None:
    _HOOKS.reset(token)


def current_compose_stream() -> ComposeStream | None:
    return _HOOKS.get()
