"""Liveness and readiness probes."""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from education_platform.core.config import Settings, get_settings
from education_platform.main import app

# A realistic async URL. The failure response must not echo any of it.
_SECRET_URL = "postgresql+asyncpg://education:super-secret-password@db.internal:5432/education"

# deploy/docker-compose.prod.yml healthcheck timeout.
_COMPOSE_HEALTHCHECK_TIMEOUT_SECONDS = 5


def test_default_ready_timeout_fits_compose_healthcheck() -> None:
    settings = Settings(_env_file=None)
    assert settings.health_ready_timeout_seconds == 2.0
    assert settings.health_ready_timeout_seconds < _COMPOSE_HEALTHCHECK_TIMEOUT_SECONDS


def test_health_live_is_200_without_a_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid_database(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("liveness probe used the database")

    monkeypatch.setattr("education_platform.main._seed_if_empty", lambda: None)
    monkeypatch.setattr("education_platform.db.session.get_engine", forbid_database)
    monkeypatch.setattr("education_platform.core.health.ping_database", forbid_database)

    with TestClient(app) as http:
        response = http.get("/health/live")

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


def test_ready_is_503_when_the_database_call_raises(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def explode() -> None:
        raise RuntimeError(f"could not connect to {_SECRET_URL}")

    monkeypatch.setattr("education_platform.core.health.ping_database", explode)

    with caplog.at_level(logging.ERROR):
        ready = client.get("/health/ready")
        health = client.get("/health")
        live = client.get("/health/live")

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
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def hang() -> None:
        await asyncio.sleep(30)

    monkeypatch.setenv("HEALTH_READY_TIMEOUT_SECONDS", "0.05")
    get_settings.cache_clear()
    monkeypatch.setattr("education_platform.core.health.ping_database", hang)

    with caplog.at_level(logging.ERROR):
        ready = client.get("/health/ready")
        health = client.get("/health")

    for response in (ready, health):
        assert response.status_code == 503
        assert response.json() == {"status": "degraded", "db": "error"}
        assert "super-secret-password" not in response.text
        assert "TimeoutError" not in response.text
        assert "Traceback" not in response.text
    assert "timed out" in caplog.text.lower()
