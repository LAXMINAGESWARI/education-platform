"""SSE progress stream for one lean text-to-SQL ask.

Framing matches ``modules/progress/sse.py``: ``event`` / ``data`` frames and a
``: heartbeat`` comment. The run is stored by ``(user_id, request_id)``. A
reconnect replays the stored terminal frames, or the progress snapshot while
the first run is still going. It does not execute SQL again.
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

from education_platform.modules.text_to_sql.lean import (
    Speculative,
    active_speculative,
    push_speculative,
    reset_speculative,
    run_lean,
)
from education_platform.modules.text_to_sql.run_store import (
    AskRunView,
    DbRunStore,
    RunStore,
    push_store,
    reset_store,
)
from education_platform.modules.text_to_sql.state import TextToSQLState

logger = logging.getLogger(__name__)

HEARTBEAT = b": heartbeat\n\n"
HEARTBEAT_SECONDS = 15.0
_END = object()
_BACKGROUND: set[asyncio.Task[None]] = set()


def default_store() -> RunStore:
    """The store API pods share. Tests replace this with the memory store."""
    return DbRunStore()


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
    def __init__(
        self,
        request_id: str,
        queue: asyncio.Queue[bytes | object],
        store: RunStore | None = None,
        user_id: str = "",
    ) -> None:
        self._request_id = request_id
        self._queue = queue
        self._store = store
        self._user_id = user_id
        self._seq = 0
        self._closed = False

    async def _progress(self, payload: dict[str, Any]) -> None:
        if self._store is not None:
            await self._store.append_progress(self._user_id, self._request_id, payload)

    async def _terminal(self, event: str, payload: dict[str, Any], *, close: bool) -> None:
        if self._store is not None:
            await self._store.append_terminal(
                self._user_id, self._request_id, event, payload, close=close
            )

    async def progress(self, stage: str, status: str) -> None:
        if self._closed:
            return
        self._seq += 1
        message = _STAGE_MESSAGE[stage] if status == "running" else None
        payload = {
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
        }
        await self._progress(payload)
        await self._queue.put(encode_event("progress", payload))

    async def table(self, columns: list[str], rows: list[list[Any]]) -> None:
        if self._closed:
            return
        payload = {"columns": columns, "rows": rows}
        await self._terminal("table", payload, close=False)
        await self._queue.put(encode_event("table", payload))

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
        payload = {
            "natural_answer": natural_answer,
            "confidence": confidence,
            "provenance": provenance,
        }
        await self._terminal("final", payload, close=True)
        await self._queue.put(encode_event("final", payload))

    async def refusal(self, message: str) -> None:
        if self._closed:
            return
        self._closed = True
        payload = {"message": message}
        await self._terminal("refusal", payload, close=True)
        await self._queue.put(encode_event("refusal", payload))

    async def error(self, message: str) -> None:
        if self._closed:
            return
        self._closed = True
        payload = {"message": message}
        await self._terminal("error", payload, close=True)
        await self._queue.put(encode_event("error", payload))


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


def _terminal_bytes(row: AskRunView) -> list[bytes]:
    frames: list[bytes] = []
    for item in row.terminal:
        event = item.get("event")
        data = item.get("data")
        if isinstance(event, str) and isinstance(data, dict):
            frames.append(encode_event(event, data))
    return frames


def _progress_bytes(row: AskRunView) -> list[bytes]:
    return [encode_event("progress", payload) for payload in row.progress]


async def _pause(
    *,
    heartbeat_seconds: float,
    clock: Callable[[], float],
    wait: WaitFn,
) -> None:
    started = clock()
    sleeper: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(heartbeat_seconds))
    try:
        while True:
            remaining = heartbeat_seconds - (clock() - started)
            if remaining <= 0:
                return
            done = await wait({sleeper}, remaining)
            if sleeper in done:
                return
    finally:
        if not sleeper.done():
            sleeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sleeper


async def _follow(
    store: RunStore,
    *,
    user_id: str,
    request_id: str,
    timeout_message: str,
    heartbeat_seconds: float,
    clock: Callable[[], float],
    wait: WaitFn,
) -> AsyncIterator[bytes]:
    """Snapshot, then heartbeats, then the terminal frames. No second run."""
    row = await store.load(user_id, request_id)
    if row is None or row.expired:
        yield encode_event("error", {"message": timeout_message})
        return
    for frame in _progress_bytes(row):
        yield frame
    if row.status == "done":
        for frame in _terminal_bytes(row):
            yield frame
        return
    while True:
        await _pause(heartbeat_seconds=heartbeat_seconds, clock=clock, wait=wait)
        yield HEARTBEAT
        row = await store.load(user_id, request_id)
        if row is None or row.expired:
            yield encode_event("error", {"message": timeout_message})
            return
        if row.status == "done":
            for frame in _terminal_bytes(row):
                yield frame
            return


def _detach(producer: asyncio.Task[None]) -> None:
    _BACKGROUND.add(producer)
    producer.add_done_callback(_BACKGROUND.discard)


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
    store: RunStore | None = None,
) -> AsyncIterator[bytes]:
    """Bytes for one ask. A second caller with the same id replays the stored run."""
    runs = store or default_store()
    user_id = str(state.get("user_id") or "")
    waiter = wait or _default_wait
    existing = await runs.load(user_id, request_id)
    if existing is not None and not existing.expired:
        if existing.status == "done":
            for frame in _terminal_bytes(existing):
                yield frame
            return
        async for frame in _follow(
            runs,
            user_id=user_id,
            request_id=request_id,
            timeout_message=timeout_message,
            heartbeat_seconds=heartbeat_seconds,
            clock=clock,
            wait=waiter,
        ):
            yield frame
        return
    if not await runs.claim(user_id, request_id):
        async for frame in _follow(
            runs,
            user_id=user_id,
            request_id=request_id,
            timeout_message=timeout_message,
            heartbeat_seconds=heartbeat_seconds,
            clock=clock,
            wait=waiter,
        ):
            yield frame
        return

    queue: asyncio.Queue[bytes | object] = asyncio.Queue()
    encoder = _Encoder(request_id, queue, runs, user_id)
    held = _Held()
    store_token = push_store(runs)
    spec = active_speculative() or Speculative()
    spec_token = push_speculative(spec)

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
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None:
            while current.cancelling():
                current.uncancel()
    finally:
        reset_store(store_token)
        try:
            if not producer.done():
                logger.info("text_to_sql.ask_stream: client disconnected request_id=%s", request_id)
                await spec.cancel()
                _detach(producer)
        finally:
            reset_speculative(spec_token)
