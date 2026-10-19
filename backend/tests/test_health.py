"""Liveness and readiness probes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator, Mapping

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from education_platform.core.config import Settings, get_settings
from education_platform.core.health import ping_database
from education_platform.db.session import get_engine, reset_engine
from education_platform.main import app

# A realistic async URL. The failure response must not echo any of it.
_SECRET_URL = "postgresql+asyncpg://education:super-secret-password@db.internal:5432/education"

# deploy/docker-compose.prod.yml healthcheck timeout.
_COMPOSE_HEALTHCHECK_TIMEOUT_SECONDS = 5


def test_default_ready_timeout_fits_compose_healthcheck() -> None:
    settings = Settings(_env_file=None)
    assert settings.health_ready_timeout_seconds == 2.0
    assert settings.health_ready_timeout_seconds < _COMPOSE_HEALTHCHECK_TIMEOUT_SECONDS


def _connect_kwargs(engine: AsyncEngine) -> Mapping[str, object]:
    creator = engine.sync_engine.pool._creator  # type: ignore[attr-defined]
    assert creator.__closure__ is not None
    for cell in creator.__closure__:
        contents = cell.cell_contents
        if isinstance(contents, Mapping) and "timeout" in contents:
            return contents
    raise AssertionError("asyncpg connect arguments were not recorded on the engine")


def test_engine_bounds_pool_checkout_and_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEALTH_READY_TIMEOUT_SECONDS", "1.25")
    monkeypatch.setenv("DATABASE_URL", _SECRET_URL)
    get_settings.cache_clear()
    reset_engine()
    try:
        engine = get_engine()
        assert engine.pool.timeout() == 1.25
        assert _connect_kwargs(engine)["timeout"] == 1.25
    finally:
        reset_engine()
        get_settings.cache_clear()


@pytest.fixture()
def probe_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """HTTP client whose lifespan and liveness probe do not open Postgres."""

    def forbid_database(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("health probe used the database")

    monkeypatch.setattr("education_platform.main._seed_if_empty", lambda: None)
    monkeypatch.setattr("education_platform.db.session.get_engine", forbid_database)
    monkeypatch.setattr("education_platform.core.health.get_engine", forbid_database)
    with TestClient(app) as http:
        yield http


def test_health_live_is_200_without_a_database(probe_client: TestClient) -> None:
    response = probe_client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "super-secret-password" not in response.text


def test_health_and_ready_are_ok_with_the_test_database(client: TestClient) -> None:
    ready = client.get("/health/ready")
    health = client.get("/health")

    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "db": "ok"}
    assert health.status_code == 200
    assert health.json() == ready.json()


def test_ready_is_200_when_the_ping_succeeds(
    probe_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ok() -> None:
        return None

    monkeypatch.setattr("education_platform.core.health.ping_database", ok)
    ready = probe_client.get("/health/ready")
    health = probe_client.get("/health")
    live = probe_client.get("/health/live")

    for response in (ready, health):
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "db": "ok"}
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}


def test_ready_is_503_when_the_database_call_raises(
    probe_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def explode() -> None:
        raise RuntimeError(f"could not connect to {_SECRET_URL}")

    monkeypatch.setattr("education_platform.core.health.ping_database", explode)

    with caplog.at_level(logging.ERROR):
        ready = probe_client.get("/health/ready")
        health = probe_client.get("/health")
        live = probe_client.get("/health/live")

    for response in (ready, health):
        assert response.status_code == 503
        assert response.json() == {"status": "degraded", "db": "error"}
        assert _SECRET_URL not in response.text
        assert "super-secret-password" not in response.text
        assert "RuntimeError" not in response.text
        assert "db.internal" not in response.text
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert _SECRET_URL in caplog.text


def test_ready_is_503_when_the_database_call_times_out(
    probe_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def hang() -> None:
        await asyncio.sleep(30)

    monkeypatch.setenv("HEALTH_READY_TIMEOUT_SECONDS", "0.05")
    get_settings.cache_clear()
    monkeypatch.setattr("education_platform.core.health.ping_database", hang)

    try:
        with caplog.at_level(logging.ERROR):
            ready = probe_client.get("/health/ready")
            health = probe_client.get("/health")
    finally:
        get_settings.cache_clear()

    for response in (ready, health):
        assert response.status_code == 503
        assert response.json() == {"status": "degraded", "db": "error"}
        assert "super-secret-password" not in response.text
        assert "TimeoutError" not in response.text
        assert "Traceback" not in response.text
    assert "timed out" in caplog.text.lower()


@pytest.mark.asyncio
async def test_ping_sets_command_timeout_only_for_the_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Driver:
        def __init__(self) -> None:
            self.command_timeout: float | None = None

    class _Raw:
        def __init__(self, driver: _Driver) -> None:
            self.driver_connection = driver

    class _Connection:
        def __init__(self) -> None:
            self.driver = _Driver()
            self.closed = False

        async def get_raw_connection(self) -> _Raw:
            return _Raw(self.driver)

        async def execute(self, _statement: object) -> None:
            assert self.driver.command_timeout == 0.4

        async def close(self) -> None:
            self.closed = True

    connection = _Connection()

    class _Engine:
        async def connect(self) -> _Connection:
            return connection

    monkeypatch.setenv("HEALTH_READY_TIMEOUT_SECONDS", "0.4")
    get_settings.cache_clear()
    monkeypatch.setattr("education_platform.core.health.get_engine", lambda: _Engine())
    try:
        await ping_database()
    finally:
        get_settings.cache_clear()

    assert connection.closed
    assert connection.driver.command_timeout is None
