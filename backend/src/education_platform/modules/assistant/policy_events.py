"""Optional progress hook for one policy turn.

The one-shot message route leaves this unset. The SSE route sets it for the
producer task so the graph can emit stages without importing the stream.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Protocol


class PolicyEvents(Protocol):
    async def stage(self, stage: str, status: str, message: str | None = None) -> None: ...

    async def delta(self, text: str) -> None: ...


_events: ContextVar[PolicyEvents | None] = ContextVar("policy_chat_events", default=None)


def current_policy_events() -> PolicyEvents | None:
    return _events.get()


def push_policy_events(events: PolicyEvents) -> Token[PolicyEvents | None]:
    return _events.set(events)


def reset_policy_events(token: Token[PolicyEvents | None]) -> None:
    _events.reset(token)
