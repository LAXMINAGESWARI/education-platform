"""Database readiness probe.

Runs ``SELECT 1`` on the shared async engine and returns a fixed body so
connection strings and driver errors never leave the server.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from education_platform.core.config import get_settings
from education_platform.db.session import get_engine

logger = logging.getLogger(__name__)

_READY_OK: dict[str, str] = {"status": "ok", "db": "ok"}
_READY_DEGRADED: dict[str, str] = {"status": "degraded", "db": "error"}


async def ping_database() -> None:
    """Open a connection on the application engine and run ``SELECT 1``."""
    engine = get_engine()
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def readiness_response() -> JSONResponse:
    """Return 200 when Postgres answers, otherwise 503 with a fixed body.

    Failures and timeouts are logged as warnings with the exception class and
    message. The response body is one of the two constants above.
    """
    timeout_seconds = get_settings().health_ready_timeout_seconds
    try:
        await asyncio.wait_for(ping_database(), timeout=timeout_seconds)
    except TimeoutError as exc:
        logger.warning(
            "Readiness database check timed out after %s seconds (%s: %s)",
            timeout_seconds,
            type(exc).__name__,
            exc,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_READY_DEGRADED,
        )
    except Exception as exc:
        logger.warning(
            "Readiness database check failed (%s: %s)",
            type(exc).__name__,
            exc,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_READY_DEGRADED,
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content=_READY_OK)
