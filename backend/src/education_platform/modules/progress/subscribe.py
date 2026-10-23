from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.core.errors import DomainError
from education_platform.modules.authorization.principal import Principal
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.generation.schemas import GenerationRunOut
from education_platform.modules.progress.close import should_close
from education_platform.modules.progress.events import ProgressEvent, progress_for_snapshot
from education_platform.modules.progress.snapshots import PostgresSnapshotReader
from education_platform.modules.progress.types import (
    CloseFrame,
    ErrorFrame,
    EventId,
    HeartbeatFrame,
    ProgressEventFrame,
    ProgressFrame,
    ProgressSnapshot,
    ProgressSubject,
    SnapshotFrame,
    event_id_for,
)
from education_platform.modules.progress.wake import PostgresWakeWaiter, WakeResult, WakeWaiter

HEARTBEAT_SECONDS = 15.0


def _event_id(
    subject: ProgressSubject,
    snapshot: ProgressSnapshot,
    progress: ProgressEvent | None,
) -> EventId:
    """Identity for one snapshot/progress pair.

    A version snapshot already includes the stage, so its hash moves with the
    stage. A generation run does not. While intake is indexing, fold the upload
    seq into the id so a stage wake still emits ``progress`` when the run row
    is unchanged. Both frames of the pair share that id.
    """
    base = event_id_for(subject, snapshot)
    if progress is None or not isinstance(snapshot, GenerationRunOut):
        return base
    return EventId(f"{base.value}:{progress.seq}")


class SnapshotReader(Protocol):
    async def authorize_and_read(
        self,
        principal: Principal,
        scope: Scope,
        subject: ProgressSubject,
        *,
        heal: bool,
    ) -> ProgressSnapshot: ...


async def subscribe(
    principal: Principal,
    scope: Scope,
    subject: ProgressSubject,
    *,
    last_event_id: EventId | None = None,
    peek_session: AsyncSession | None = None,
    reader: SnapshotReader | None = None,
    wakes: WakeWaiter | None = None,
) -> AsyncIterator[ProgressFrame]:
    """Yield snapshot/heartbeat/close frames until the run or version is terminal."""
    snapshot_reader = reader or PostgresSnapshotReader(peek_session=peek_session)
    waiter = wakes or PostgresWakeWaiter()
    last = last_event_id
    heal = True
    opened = False
    try:
        await waiter.wait(subject, timeout=0)
        while True:
            try:
                snapshot = await snapshot_reader.authorize_and_read(
                    principal, scope, subject, heal=heal
                )
            except DomainError as exc:
                if last is None:
                    raise
                yield ErrorFrame(status=exc.status_code, detail=exc.detail)
                return
            heal = False
            progress = progress_for_snapshot(snapshot)
            if progress is None and isinstance(snapshot_reader, PostgresSnapshotReader):
                progress = await snapshot_reader.intake_upload_progress(principal, snapshot)
            eid = _event_id(subject, snapshot, progress)
            closing = should_close(snapshot)
            if last is None or eid != last:
                last = eid
                opened = True
                yield SnapshotFrame(event_id=eid, snapshot=snapshot, close_after=closing)
                if progress is not None:
                    yield ProgressEventFrame(event_id=eid, event=progress)
            elif not opened and not closing:
                # Opening resume already has this id, so the next wait would be
                # the 15s heartbeat. A comment here lets the router flush
                # headers before that wait. Later unchanged polls stay quiet.
                opened = True
                yield HeartbeatFrame(comment="connected")
            if closing:
                yield CloseFrame(reason="terminal")
                return
            wait = await waiter.wait(subject, timeout=HEARTBEAT_SECONDS)
            if wait is WakeResult.TIMED_OUT:
                yield HeartbeatFrame()
    finally:
        await waiter.aclose()
