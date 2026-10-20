"""Five-second Postgres connect timeout for the test process.

Libpq waits forever when ``connect_timeout`` / ``PGCONNECT_TIMEOUT`` is unset, so a
black-holed database hangs the suite. asyncpg ignores ``PGCONNECT_TIMEOUT`` and
defaults to 60s; its knob is ``timeout``.

Production engines are left alone. CI sets ``PGCONNECT_TIMEOUT`` for the Alembic
step, which does not import this module.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import asyncpg
import sqlalchemy.ext.asyncio as sqlalchemy_asyncio

from education_platform.db import session as db_session

CONNECT_TIMEOUT_SECONDS = 5

_installed = False


def sync_connect_args(existing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(existing or {})
    merged.setdefault("connect_timeout", CONNECT_TIMEOUT_SECONDS)
    return merged


def async_connect_args(existing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(existing or {})
    merged.setdefault("timeout", CONNECT_TIMEOUT_SECONDS)
    return merged


def install_test_connect_timeouts() -> None:
    """Apply the connect cap for every later connection in this process."""
    global _installed
    os.environ.setdefault("PGCONNECT_TIMEOUT", str(CONNECT_TIMEOUT_SECONDS))
    if _installed:
        return
    _installed = True

    original_create_async_engine = sqlalchemy_asyncio.create_async_engine
    original_asyncpg_connect = asyncpg.connect

    def create_async_engine(*args: Any, **kwargs: Any) -> Any:
        kwargs["connect_args"] = async_connect_args(kwargs.get("connect_args"))
        return original_create_async_engine(*args, **kwargs)

    async def asyncpg_connect(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", CONNECT_TIMEOUT_SECONDS)
        return await original_asyncpg_connect(*args, **kwargs)

    create_async_engine.connect_timeout_seconds = CONNECT_TIMEOUT_SECONDS  # type: ignore[attr-defined]

    sqlalchemy_asyncio.create_async_engine = create_async_engine  # type: ignore[assignment]
    db_session.create_async_engine = create_async_engine  # type: ignore[assignment]
    asyncpg.connect = asyncpg_connect  # type: ignore[assignment]
