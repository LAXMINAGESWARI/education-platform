"""Shared Postgres fixtures for database and API tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from tests.postgres_connect import (
    async_connect_args,
    install_test_connect_timeouts,
    sync_connect_args,
)

from education_platform.core.config import get_settings
from education_platform.core.errors import DomainError
from education_platform.db import models as _models
from education_platform.db.session import get_session, reset_engine
from education_platform.db.url import to_async_url, to_sync_url
from education_platform.main import app
from education_platform.modules.materials.seed import seed_approved_materials
from education_platform.modules.text_to_sql.nodes.load_schema import (
    _load_filtered_schema_context,
)

_ = _models

# Test-process only. Production session engines are unchanged.
install_test_connect_timeouts()

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
MATERIALS_DIR = REPO_ROOT / "docs" / "curriculum"

STUDENT_EMAIL = "student@example.com"
STUDENT_PASSWORD = "password123"

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://education:education@localhost:5432/education_test"


@pytest.fixture(autouse=True)
def _stub_openrouter_unless_live(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the default suite off a live OpenRouter key from backend/.env.

    Production code treats an empty key as unconfigured (heuristic / stub paths).
    Opt-in live evals set ``RUN_LIVE_LLM_TESTS=1`` (see
    ``test_text_to_sql_generate_sql_live.py``). An empty env value must be set —
    deleting the var would let pydantic-settings fall back to ``.env`` again.
    """
    if os.environ.get("RUN_LIVE_LLM_TESTS") == "1":
        yield
        return
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_schema_context_cache() -> Iterator[None]:
    """Not a Postgres fixture — lives here (not in test_text_to_sql_load_schema.py)
    specifically so it applies file-wide, not just to that one test module. Tests that
    monkeypatch `load_schema`'s `SCHEMA_CATALOG_PATH` to simulate a missing/malformed
    file rely on this: `_load_filtered_schema_context` is `@lru_cache`d, and a stale
    cache from an earlier *successful* read silently masks a later failure test instead
    of raising — confirmed by deliberately disabling this fixture and watching that
    exact failure mode reproduce. Any future test that exercises the real
    text-to-SQL graph (not just this node in isolation) needs the same protection.
    """
    _load_filtered_schema_context.cache_clear()
    yield
    _load_filtered_schema_context.cache_clear()


def _test_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    """Migrate education_test once per session; fail clearly if Postgres is down."""
    database_url = _test_database_url()
    sync_url = to_sync_url(database_url)
    previous_database_url = os.environ.get("DATABASE_URL")
    previous_jwt_secret = os.environ.get("JWT_SECRET")

    os.environ["DATABASE_URL"] = database_url
    os.environ["JWT_SECRET"] = "test-secret-key-at-least-32-bytes-long!!"
    get_settings.cache_clear()
    reset_engine()

    engine = create_engine(sync_url, pool_pre_ping=True, connect_args=sync_connect_args())
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — surface infra failures to pytest
        engine.dispose()
        pytest.fail(
            "Postgres test database unreachable. "
            "Run `docker compose up -d postgres` then retry. "
            f"Detail: {exc}"
        )

    alembic_cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(alembic_cfg, "head")

    with engine.connect() as conn:
        tables = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        ).fetchall()
        assert len(tables) >= 33

    engine.dispose()
    yield database_url

    reset_engine()
    get_settings.cache_clear()
    if previous_database_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous_database_url
    if previous_jwt_secret is None:
        os.environ.pop("JWT_SECRET", None)
    else:
        os.environ["JWT_SECRET"] = previous_jwt_secret


@pytest.fixture()
def clean_db(migrated_database: str, tmp_path: Path) -> Iterator[str]:
    """Truncate all public tables (except alembic_version) before each test."""
    database_url = migrated_database
    sync_url = to_sync_url(database_url)
    previous_upload_dir = os.environ.get("UPLOAD_DIR")
    os.environ["DATABASE_URL"] = database_url
    os.environ["UPLOAD_DIR"] = str(tmp_path / "uploads")
    get_settings.cache_clear()
    reset_engine()

    engine = create_engine(sync_url, pool_pre_ping=True, connect_args=sync_connect_args())
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).fetchall()
        table_names = [row[0] for row in rows]
        if table_names:
            quoted = ", ".join(f'"{name}"' for name in table_names)
            conn.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
    engine.dispose()
    yield database_url

    reset_engine()
    get_settings.cache_clear()
    if previous_upload_dir is None:
        os.environ.pop("UPLOAD_DIR", None)
    else:
        os.environ["UPLOAD_DIR"] = previous_upload_dir


@pytest.fixture()
def db_session(clean_db: str) -> Iterator[Session]:
    engine = create_engine(
        to_sync_url(clean_db), pool_pre_ping=True, connect_args=sync_connect_args()
    )
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture()
def seeded_db(db_session: Session) -> Session:
    seeded = seed_approved_materials(db_session, MATERIALS_DIR, replace=True)
    assert "rectangles_squares_properties" in seeded
    assert "square_numbers_patterns" in seeded
    return db_session


@pytest_asyncio.fixture()
async def async_db_session(clean_db: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        to_async_url(clean_db),
        pool_pre_ping=True,
        connect_args=async_connect_args(),
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture()
def client(seeded_db: Session, clean_db: str) -> Iterator[TestClient]:
    """HTTP client bound to a migrated + seeded Postgres database."""
    _ = seeded_db
    engine = create_async_engine(
        to_async_url(clean_db),
        pool_pre_ping=True,
        connect_args=async_connect_args(),
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except DomainError:
                await session.commit()
                raise
            except Exception:
                await session.rollback()
                raise
            else:
                await session.commit()

    app.dependency_overrides[get_session] = _override_session
    try:
        with TestClient(app, raise_server_exceptions=True) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        engine.sync_engine.dispose()
        reset_engine()


@pytest.fixture()
def enrolled_student_headers(client: TestClient) -> dict[str, str]:
    provision = client.post(
        "/api/v1/auth/provision-student",
        json={
            "email": STUDENT_EMAIL,
            "password": STUDENT_PASSWORD,
            "full_name": "Test Student",
            "student_identifier": "S-100",
            "institution_name": "POC Demo School",
        },
    )
    assert provision.status_code == 200, provision.text
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": STUDENT_EMAIL,
            "password": STUDENT_PASSWORD,
            "institution_name": "POC Demo School",
        },
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    enroll = client.post(
        "/api/v1/me/enrollments/poc-math",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert enroll.status_code == 200, enroll.text
    return {"Authorization": f"Bearer {token}"}
