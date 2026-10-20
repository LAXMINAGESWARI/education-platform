"""SSE progress stream for one lean text-to-SQL ask.

Framing matches ``modules/progress/sse.py``: ``event`` / ``data`` frames and a
``: heartbeat`` comment. There is no replay. A dropped client re-posts the same
``request_id``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from education_platform.modules.text_to_sql.lean import run_lean
from education_platform.modules.text_to_sql.state import TextToSQLState

logger = logging.getLogger(__name__)

HEARTBEAT = b": heartbeat\n\n"
HEARTBEAT_SECONDS = 15.0
_END = object()

_STAGE_MESSAGE = {
    "checking": "Checking your question",
    "understanding": "Understanding your question",
    "writing_query": "Writing a query",
    "validating": "Checking the query",
    "running": "Looking up the data",
    "summarising": "Writing the answer",
}

WaitFn = Callable[[set[asyncio.Task[Any]], float], Awaitable[set[asyncio.Task[Any]]]]


def encode_event(name: str, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return f"event: {name}\ndata: {data}\n\n".encode()


class _Encoder:
    def __init__(self, request_id: str, queue: asyncio.Queue[bytes | object]) -> None:
        self._request_id = request_id
        self._queue = queue
        self._seq = 0
        self._closed = False

    async def progress(self, stage: str, status: str) -> None:
        if self._closed:
            return
        self._seq += 1
        message = _STAGE_MESSAGE[stage] if status == "running" else None
        await self._queue.put(
            encode_event(
                "progress",
                {
                    "v": 1,
                    "kind": "text_to_sql",
                    "subject_id": self._request_id,
                    "seq": self._seq,
                    "stage": stage,
                    "status": status,
                    "percent": None,
                    "current": None,
                    "total": None,
                    "unit": None,
                    "message": message,
                    "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )
        )

    async def table(self, columns: list[str], rows: list[list[Any]]) -> None:
        if self._closed:
            return
        await self._queue.put(encode_event("table", {"columns": columns, "rows": rows}))

    async def delta(self, text: str) -> None:
        if self._closed:
            return
        await self._queue.put(encode_event("answer_delta", {"text": text}))

    async def final(
        self, natural_answer: str, confidence: str | None, provenance: str | None
    ) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(
            encode_event(
                "final",
                {
                    "natural_answer": natural_answer,
                    "confidence": confidence,
                    "provenance": provenance,
                },
            )
        )

    async def refusal(self, message: str) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(encode_event("refusal", {"message": message}))

    async def error(self, message: str) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(encode_event("error", {"message": message}))


async def _default_wait(tasks: set[asyncio.Task[Any]], timeout: float) -> set[asyncio.Task[Any]]:
    done, _pending = await asyncio.wait(tasks, timeout=timeout)
    return done


class _Held:
    """A frame taken off the queue in the same moment a heartbeat was due."""

    def __init__(self) -> None:
        self.item: bytes | object | None = None


async def _next_frame(
    queue: asyncio.Queue[bytes | object],
    *,
    held: _Held,
    heartbeat_seconds: float,
    clock: Callable[[], float],
    wait: WaitFn,
) -> bytes | object:
    if held.item is not None:
        item = held.item
        held.item = None
        return item
    started = clock()
    getter: asyncio.Task[bytes | object] = asyncio.create_task(queue.get())
    heartbeat = False
    delivered = False
    try:
        while True:
            remaining = heartbeat_seconds - (clock() - started)
            if remaining <= 0:
                if getter.done() and not getter.cancelled():
                    delivered = True
                    return getter.result()
                heartbeat = True
                return HEARTBEAT
            done = await wait({getter}, remaining)
            if getter in done:
                delivered = True
                return getter.result()
    finally:
        if not getter.done():
            getter.cancel()
        if not delivered:
            with contextlib.suppress(asyncio.CancelledError):
                item = await getter
                if heartbeat:
                    held.item = item


async def iter_ask_stream(
    state: TextToSQLState,
    *,
    request_id: str,
    timeout_seconds: float,
    timeout_message: str,
    unexpected_message: str,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    wait: WaitFn | None = None,
    result_box: dict[str, TextToSQLState] | None = None,
) -> AsyncIterator[bytes]:
    """Bytes for one ask. Heartbeats fill quiet gaps. The run is not replayed."""
    queue: asyncio.Queue[bytes | object] = asyncio.Queue()
    encoder = _Encoder(request_id, queue)
    waiter = wait or _default_wait
    held = _Held()

    async def produce() -> None:
        try:
            result = await asyncio.wait_for(run_lean(state, encoder), timeout=timeout_seconds)
            if result_box is not None:
                result_box["result"] = result
        except TimeoutError:
            logger.error("text_to_sql.ask_stream: exceeded %.0fs", timeout_seconds)
            await encoder.error(timeout_message)
        except Exception:
            logger.exception("text_to_sql.ask_stream: unhandled exception")
            await encoder.error(unexpected_message)
        finally:
            await queue.put(_END)

    producer = asyncio.create_task(produce())
    try:
        while True:
            item = await _next_frame(
                queue,
                held=held,
                heartbeat_seconds=heartbeat_seconds,
                clock=clock,
                wait=waiter,
            )
            if item is _END:
                break
            if item is HEARTBEAT:
                yield HEARTBEAT
                continue
            if isinstance(item, bytes):
                yield item
    finally:
        if not producer.done():
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer
