"""SSE progress for one policy chat turn.

Stages are checking, searching, and summarising. The terminal frame is
``answer_delta`` plus ``final`` or ``refusal``. A repeat POST with the same
``request_id`` replays the saved turn and does not insert another message.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from education_platform.core.errors import DomainError
from education_platform.db.session import get_session_factory
from education_platform.modules.assistant import service
from education_platform.modules.assistant.policy_events import (
    push_policy_events,
    reset_policy_events,
)
from education_platform.modules.assistant.schemas import PostMessageOut
from education_platform.modules.assistant.strategy import (
    AssistantKind,
    PolicyAssistantContext,
    graph_for,
)
from education_platform.modules.authorization.principal import Principal
from education_platform.modules.text_to_sql.stream import (
    HEARTBEAT,
    HEARTBEAT_SECONDS,
    _default_wait,
    _Held,
    _next_frame,
    encode_event,
)

logger = logging.getLogger(__name__)

_END = object()
_MAX_RUNS = 200
_UNEXPECTED = "Something went wrong on our end — please try again."
_STAGE_MESSAGE = {
    "checking": "Checking your question",
    "searching": "Searching policy documents",
    "summarising": "Writing the answer",
}
_STAGE_PERCENT = {"checking": 25, "searching": 60, "summarising": 90}

_LOCK = asyncio.Lock()
_RUNS: dict[tuple[str, str, str], _Run] = {}
_BACKGROUND: set[asyncio.Task[None]] = set()


class _Run:
    def __init__(self) -> None:
        self.done = asyncio.Event()
        self.frames: list[bytes] = []


class _Encoder:
    def __init__(self, request_id: UUID, queue: asyncio.Queue[bytes | object], run: _Run) -> None:
        self.request_id = str(request_id)
        self.refused = False
        self._queue = queue
        self._run = run
        self._seq = 0
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def stage(self, stage: str, status: str, message: str | None = None) -> None:
        if self._closed:
            return
        if stage == "checking" and status == "failed":
            self.refused = True
        phrase = message
        if phrase is None and status == "running":
            phrase = _STAGE_MESSAGE.get(stage)
        percent: int | None
        if status == "done":
            percent = 100
        elif status == "failed":
            percent = None
        else:
            percent = _STAGE_PERCENT.get(stage)
        self._seq += 1
        await self._queue.put(
            encode_event(
                "progress",
                {
                    "v": 1,
                    "kind": "policy_chat",
                    "subject_id": self.request_id,
                    "seq": self._seq,
                    "stage": stage,
                    "status": status,
                    "percent": percent,
                    "current": None,
                    "total": None,
                    "unit": None,
                    "message": phrase if status == "running" else None,
                    "updated_at": _now(),
                },
            )
        )

    async def delta(self, text: str) -> None:
        if self._closed or not text:
            return
        await self._queue.put(encode_event("answer_delta", {"text": text}))

    async def final(self, saved: PostMessageOut) -> None:
        await self._finish("final", _final_payload(saved))

    async def refusal(self, saved: PostMessageOut) -> None:
        await self._finish(
            "refusal",
            {"message": saved.assistant_message.content, **saved.model_dump(mode="json")},
        )

    async def error(self, message: str) -> None:
        await self._finish("error", {"message": message})

    async def _finish(self, name: str, payload: dict[str, Any]) -> None:
        if self._closed:
            return
        self._closed = True
        frame = encode_event(name, payload)
        self._run.frames.append(frame)
        await self._queue.put(frame)
        self._run.done.set()


def clear_policy_runs() -> None:
    """Drop the process-local replay cache. Tests use this between requests."""
    _RUNS.clear()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _final_payload(saved: PostMessageOut) -> dict[str, Any]:
    citations = saved.assistant_message.citations or []
    return {
        "content": saved.assistant_message.content,
        "citations": [item.model_dump(mode="json") for item in citations],
        **saved.model_dump(mode="json"),
    }


def _evict_finished_runs() -> None:
    if len(_RUNS) <= _MAX_RUNS:
        return
    for key, run in list(_RUNS.items()):
        if run.done.is_set():
            del _RUNS[key]
        if len(_RUNS) <= _MAX_RUNS:
            return


async def _claim(user_id: UUID, conversation_id: UUID, request_id: UUID) -> tuple[_Run, bool]:
    """One live run per conversation. A client may reuse a request id elsewhere."""
    key = (str(user_id), str(conversation_id), str(request_id))
    async with _LOCK:
        current = _RUNS.get(key)
        if current is not None:
            return current, False
        run = _Run()
        _RUNS[key] = run
        _evict_finished_runs()
        return run, True


def _detach(producer: asyncio.Task[None]) -> None:
    _BACKGROUND.add(producer)
    producer.add_done_callback(_BACKGROUND.discard)


async def _follow(run: _Run, heartbeat_seconds: float) -> AsyncIterator[bytes]:
    while not run.done.is_set():
        try:
            await asyncio.wait_for(run.done.wait(), timeout=heartbeat_seconds)
        except TimeoutError:
            yield HEARTBEAT
    for frame in run.frames:
        yield frame


async def _execute(
    encoder: _Encoder,
    *,
    principal: Principal,
    conversation_id: UUID,
    content: str,
    request_id: UUID,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        conv = await service.owned_conversation(session, principal, conversation_id)
        loaded = await service.load_policy_stream(
            session,
            conv,
            content=content,
            request_id=request_id,
        )
        if loaded.replay is not None:
            await encoder.final(loaded.replay)
            return
        message = loaded.existing_user.content if loaded.existing_user is not None else content
        token = push_policy_events(encoder)
        try:
            reply = await graph_for(AssistantKind.POLICY).run_turn(
                session=session,
                principal=principal,
                context=PolicyAssistantContext(
                    kind="policy",
                    institution_id=principal.institution_id,
                ),
                history=tuple(loaded.history),
                message=message,
            )
        finally:
            reset_policy_events(token)
        try:
            saved = await service.persist_policy_turn(
                session,
                conv,
                text=message,
                history=loaded.history,
                reply=reply,
                client_request_id=request_id,
                existing_user=loaded.existing_user,
            )
            await session.commit()
        except IntegrityError:
            await session.rollback()
            replayed = await _replay_committed(principal, conversation_id, request_id)
            if replayed is None:
                raise
            await encoder.final(replayed)
            return
        if encoder.refused:
            await encoder.refusal(saved)
        else:
            await encoder.final(saved)


async def _replay_committed(
    principal: Principal,
    conversation_id: UUID,
    request_id: UUID,
) -> PostMessageOut | None:
    factory = get_session_factory()
    async with factory() as session:
        conv = await service.owned_conversation(session, principal, conversation_id)
        loaded = await service.load_policy_stream(
            session,
            conv,
            content="",
            request_id=request_id,
        )
        await session.commit()
        return loaded.replay


async def iter_policy_stream(
    *,
    principal: Principal,
    conversation_id: UUID,
    content: str,
    request_id: UUID,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[bytes]:
    run, owner = await _claim(principal.user_id, conversation_id, request_id)
    if not owner:
        async for frame in _follow(run, heartbeat_seconds):
            yield frame
        return

    queue: asyncio.Queue[bytes | object] = asyncio.Queue()
    encoder = _Encoder(request_id, queue, run)

    async def produce() -> None:
        try:
            await _execute(
                encoder,
                principal=principal,
                conversation_id=conversation_id,
                content=content,
                request_id=request_id,
            )
        except DomainError as exc:
            await encoder.error(exc.detail)
        except Exception:
            logger.exception(
                "policy_chat.stream failed conversation_id=%s request_id=%s",
                conversation_id,
                request_id,
            )
            await encoder.error(_UNEXPECTED)
        finally:
            if not encoder.closed:
                await encoder.error(_UNEXPECTED)
            await queue.put(_END)

    producer = asyncio.create_task(produce())
    held = _Held()
    try:
        while True:
            item = await _next_frame(
                queue,
                held=held,
                heartbeat_seconds=heartbeat_seconds,
                clock=time.monotonic,
                wait=_default_wait,
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
        if not producer.done():
            logger.info("policy_chat.stream: client disconnected request_id=%s", request_id)
            _detach(producer)
