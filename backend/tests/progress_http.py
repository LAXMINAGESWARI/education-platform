"""Local HTTP server for tests that must observe SSE bytes as they flush."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn

from education_platform.db import session as db_session
from education_platform.main import app


@contextmanager
def open_progress_server() -> Iterator[str]:
    """Serve the real app on a local port so SSE bytes flush as they are written."""
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="warning",
        access_log=False,
        lifespan="off",
    )
    server = uvicorn.Server(config)

    def _run() -> None:
        async def _main() -> None:
            try:
                await server.serve()
            finally:
                engine = db_session._engine
                db_session._engine = None
                db_session._session_factory = None
                db_session._bound_url = None
                if engine is not None:
                    await engine.dispose()

        asyncio.run(_main())

    thread = threading.Thread(target=_run, name="progress-sse", daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    listeners = getattr(server, "servers", None)
    sockets = listeners[0].sockets if server.started and listeners else None
    if sockets is None:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("progress server did not start")
    port = sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
