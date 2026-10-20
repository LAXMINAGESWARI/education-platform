"""Liveness and readiness probes."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from education_platform.core.config import Settings, get_settings
from education_platform.db.session import get_engine, reset_engine
from education_platform.main import app

# A realistic async URL. The failure response must not echo any of it.
_SECRET_URL = "postgresql+asyncpg://education:super-secret-password@db.internal:5432/education"

_REPO = Path(__file__).resolve().parents[2]


def _compose_healthcheck_timeout_seconds() -> int:
    document = yaml.safe_load(
        (_REPO / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    timeout = document["services"]["api"]["healthcheck"]["timeout"]
    match = re.fullmatch(r"(\d+)s", timeout)
    assert match is not None
    return int(match.group(1))


# deploy/docker-compose.prod.yml api healthcheck timeout.
_COMPOSE_HEALTHCHECK_TIMEOUT_SECONDS = _compose_healthcheck_timeout_seconds()


def test_default_ready_timeout_fits_compose_healthcheck() -> None:
    settings = Settings(_env_file=None)
    assert settings.health_ready_timeout_seconds == 2.0
    assert settings.health_ready_timeout_seconds < _COMPOSE_HEALTHCHECK_TIMEOUT_SECONDS


def test_startup_seed_failure_still_serves_live(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("seed failed")

    monkeypatch.setattr("education_platform.main.seed_approved_materials", explode)

    with (
        caplog.at_level(logging.ERROR, logger="education_platform.main"),
        TestClient(app) as http,
    ):
        response = http.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "Startup seeding failed; continuing" in caplog.text
    assert "seed failed" in caplog.text


def test_health_live_is_200_without_a_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid_database(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("liveness probe used the database")

    monkeypatch.setattr("education_platform.main._seed_if_empty", lambda: None)
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

    with caplog.at_level(logging.WARNING):
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
    assert "RuntimeError" in caplog.text
    assert "Traceback" not in caplog.text


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

    with caplog.at_level(logging.WARNING):
        ready = client.get("/health/ready")
        health = client.get("/health")

    for response in (ready, health):
        assert response.status_code == 503
        assert response.json() == {"status": "degraded", "db": "error"}
        assert "super-secret-password" not in response.text
        assert "TimeoutError" not in response.text
        assert "Traceback" not in response.text
    assert "timed out" in caplog.text.lower()
    assert "TimeoutError" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.regression
def test_ready_is_503_against_an_unreachable_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://education:education@127.0.0.1:1/education",
    )
    monkeypatch.setenv("HEALTH_READY_TIMEOUT_SECONDS", "1")
    get_settings.cache_clear()
    reset_engine()
    monkeypatch.setattr("education_platform.main._seed_if_empty", lambda: None)
    started = time.perf_counter()
    try:
        with TestClient(app) as http:
            ready = http.get("/health/ready")
            live = http.get("/health/live")
        elapsed = time.perf_counter() - started
    finally:
        reset_engine()
        get_settings.cache_clear()

    assert elapsed < 3
    assert ready.status_code == 503
    assert ready.json() == {"status": "degraded", "db": "error"}
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}


def test_ready_uses_shared_engine_no_new_pool(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []
    real = get_engine

    def spy() -> object:
        engine = real()
        seen.append(id(engine))
        return engine

    monkeypatch.setattr("education_platform.core.health.get_engine", spy)
    for _ in range(20):
        response = client.get("/health/ready")
        assert response.status_code == 200, response.text
    assert len(seen) == 20
    assert len(set(seen)) == 1
