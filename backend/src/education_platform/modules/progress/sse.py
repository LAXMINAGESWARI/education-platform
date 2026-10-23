from __future__ import annotations

import json
from collections.abc import AsyncIterator

from education_platform.modules.progress.types import (
    CloseFrame,
    ErrorFrame,
    HeartbeatFrame,
    ProgressEventFrame,
    ProgressFrame,
    SnapshotFrame,
)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def iter_sse(frames: AsyncIterator[ProgressFrame]) -> AsyncIterator[bytes]:
    async for frame in frames:
        if isinstance(frame, SnapshotFrame):
            yield (
                f"id: {frame.event_id.value}\n"
                f"event: snapshot\n"
                f"data: {frame.snapshot.model_dump_json()}\n\n"
            ).encode()
            continue
        if isinstance(frame, ProgressEventFrame):
            yield (
                f"id: {frame.event_id.value}\n"
                f"event: progress\n"
                f"data: {frame.event.model_dump_json()}\n\n"
            ).encode()
            continue
        if isinstance(frame, HeartbeatFrame):
            yield f": {frame.comment}\n\n".encode()
            continue
        if isinstance(frame, CloseFrame):
            payload = json.dumps({"reason": frame.reason}, separators=(",", ":"))
            yield f"event: close\ndata: {payload}\n\n".encode()
            continue
        if isinstance(frame, ErrorFrame):
            payload = json.dumps(
                {"detail": frame.detail, "status": frame.status}, separators=(",", ":")
            )
            yield f"event: error\ndata: {payload}\n\n".encode()
            continue
        raise TypeError(f"Unsupported frame: {type(frame)!r}")
