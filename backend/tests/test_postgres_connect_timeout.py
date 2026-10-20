"""Connect and per-test timeouts that keep a missing database from hanging CI."""

from __future__ import annotations

import os
import socket
import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine
from tests.postgres_connect import CONNECT_TIMEOUT_SECONDS, async_connect_args, sync_connect_args

from education_platform.db import session as db_session

# RFC 5737 documentation range. It is not routed, so TCP hangs until the connect cap.
_BLACKHOLE_HOST = "192.0.2.1"


def _host_drops_tcp(host: str, port: int = 5432) -> bool:
    sock = socket.socket()
    sock.settimeout(1)
    try:
        sock.connect((host, port))
    except TimeoutError:
        return True
    except OSError:
        return False
    else:
        return False
    finally:
        sock.close()


def test_pytest_timeout_is_two_minutes(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.pluginmanager.hasplugin("timeout")
    assert int(pytestconfig.getini("timeout")) == 120


def test_pgconnect_timeout_is_five_seconds() -> None:
    assert os.environ["PGCONNECT_TIMEOUT"] == str(CONNECT_TIMEOUT_SECONDS)


def test_connect_arg_helpers_default_to_five_seconds() -> None:
    assert sync_connect_args() == {"connect_timeout": CONNECT_TIMEOUT_SECONDS}
    assert async_connect_args() == {"timeout": CONNECT_TIMEOUT_SECONDS}
    assert sync_connect_args({"connect_timeout": 9})["connect_timeout"] == 9
    assert async_connect_args({"timeout": 9, "server_settings": {}})["timeout"] == 9


def test_app_async_engine_factory_uses_the_test_timeout() -> None:
    factory = db_session.create_async_engine
    assert getattr(factory, "connect_timeout_seconds", None) == CONNECT_TIMEOUT_SECONDS


def test_sync_connect_timeout_still_reaches_postgres() -> None:
    engine = create_engine(
        "postgresql+psycopg://education:education@localhost:5432/education_test",
        pool_pre_ping=True,
        connect_args=sync_connect_args(),
    )
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()


async def test_async_connect_timeout_still_reaches_postgres() -> None:
    engine = create_async_engine(
        "postgresql+asyncpg://education:education@localhost:5432/education_test",
        pool_pre_ping=True,
    )
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


def test_libpq_connect_timeout_fails_in_about_five_seconds() -> None:
    if not _host_drops_tcp(_BLACKHOLE_HOST):
        pytest.skip(f"{_BLACKHOLE_HOST} is not a blackhole on this network")
    engine = create_engine(
        f"postgresql+psycopg://education:education@{_BLACKHOLE_HOST}:5432/education_test"
    )
    started = time.monotonic()
    try:
        with pytest.raises(OperationalError), engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    finally:
        engine.dispose()
    elapsed = time.monotonic() - started
    assert 3 <= elapsed < 12


async def test_asyncpg_connect_timeout_fails_in_about_five_seconds() -> None:
    if not _host_drops_tcp(_BLACKHOLE_HOST):
        pytest.skip(f"{_BLACKHOLE_HOST} is not a blackhole on this network")
    engine = create_async_engine(
        f"postgresql+asyncpg://education:education@{_BLACKHOLE_HOST}:5432/education_test"
    )
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()
    elapsed = time.monotonic() - started
    assert 3 <= elapsed < 12
