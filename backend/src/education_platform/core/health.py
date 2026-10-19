"""Database readiness probe.

Runs ``SELECT 1`` on the shared async engine and returns a fixed body so
connection strings and driver errors never leave the server.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol, cast

from fastapi import status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from education_platform.core.config import get_settings
from education_platform.db.session import get_engine


class _CommandTimeout(Protocol):
    command_timeout: float | None


logger = logging.getLogger(__name__)

_READY_OK: dict[str, str] = {"status": "ok", "db": "ok"}
_READY_DEGRADED: dict[str, str] = {"status": "degraded", "db": "error"}


async def ping_database() -> None:
    """Open a connection on the application engine and run ``SELECT 1``.

    The engine's pool timeout and asyncpg connect timeout are the health
    budget, because cancelling this coroutine does not unblock a checkout or
    a connect stuck in the driver thread. The SELECT gets the same budget as
    a command timeout so an accepted-but-silent server cannot outlive it.
    """
    timeout_seconds = get_settings().health_ready_timeout_seconds
    engine = get_engine()
    connection = await asyncio.wait_for(engine.connect(), timeout=timeout_seconds)
    driver: _CommandTimeout | None = None
    previous_command_timeout: float | None = None
    try:
        raw = await connection.get_raw_connection()
        candidate = getattr(raw, "driver_connection", None)
        if candidate is not None and hasattr(candidate, "command_timeout"):
            driver = cast(_CommandTimeout, candidate)
            previous_command_timeout = driver.command_timeout
            driver.command_timeout = timeout_seconds
        await asyncio.wait_for(connection.execute(text("SELECT 1")), timeout=timeout_seconds)
    finally:
        if driver is not None:
            driver.command_timeout = previous_command_timeout
        await connection.close()


async def readiness_response() -> JSONResponse:
    """Return 200 when Postgres answers, otherwise 503 with a fixed body.

    Failures and timeouts are logged here. The response body is one of the two
    constants above.
    """
    timeout_seconds = get_settings().health_ready_timeout_seconds
    try:
        await asyncio.wait_for(ping_database(), timeout=timeout_seconds)
    except TimeoutError:
        logger.exception(
            "Readiness database check timed out after %s seconds",
            timeout_seconds,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_READY_DEGRADED,
        )
    except Exception:
        logger.exception("Readiness database check failed")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_READY_DEGRADED,
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content=_READY_OK)
