"""Shared Postgres fixtures for database and API tests."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
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
from education_platform.modules.auth import security
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

# Argon2's default cost is what makes generate_school() dominate the suite.
# Tests only need a hash that verifies; production keeps PasswordHasher().
_FAST_HASHER = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
_DB_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WORKER_DATABASE_URL: str | None = None
_CLONE_WAIT_SECONDS = 60


# Docling, torch, or embedding imports, from the speedup plan and its timings.
# test_generation is mostly database time, but it imports the ingest worker.
_ML_TEST_FILES = frozenset(
    {
        "test_assistant_chat.py",
        "test_feedback.py",
        "test_generation.py",
        "test_generation_curriculum.py",
        "test_prefetch_docling_models.py",
        "test_rag_ingest.py",
        "test_selective_contracts.py",
        "test_worker_bootstrap.py",
    }
)


def pytest_configure(config: pytest.Config) -> None:
    """Register markers here. The pytest markers block in pyproject stays untouched."""
    config.addinivalue_line(
        "markers",
        "ml: docling, torch, or embedding tests (excluded from the fast CI shards)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    mark = pytest.mark.ml
    for item in items:
        if item.path.name in _ML_TEST_FILES:
            item.add_marker(mark)
    _keep_this_shard(config, items)


def pytest_collection_finish(session: pytest.Session) -> None:
    """Clone this worker's database before xdist assigns any tests.

    An idle worker still collects, then blocks inside xdist's run loop and never
    executes session fixtures. Cloning here means every worker, including one that
    is later given no tests, publishes its ready file before any test can connect
    to the template database.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker:
        _activate_worker_database(worker)


def _keep_this_shard(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep one deterministic file-hash shard when CI sets PYTEST_SHARD."""
    raw_index = os.environ.get("PYTEST_SHARD")
    raw_count = os.environ.get("PYTEST_SHARD_COUNT")
    if raw_index is None and raw_count is None:
        return
    if raw_index is None or raw_count is None:
        raise pytest.UsageError("Set both PYTEST_SHARD and PYTEST_SHARD_COUNT")
    index = int(raw_index)
    count = int(raw_count)
    if count < 1 or not 0 <= index < count:
        raise pytest.UsageError(f"PYTEST_SHARD must be in 0..{count - 1}, got {index}")
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        bucket = int(hashlib.sha256(item.path.name.encode()).hexdigest()[:8], 16) % count
        if bucket == index:
            selected.append(item)
        else:
            deselected.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected


@pytest.fixture(autouse=True)
def _cheap_password_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "_hasher", _FAST_HASHER)


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


def _quote_db(name: str) -> str:
    if _DB_IDENT.fullmatch(name) is None:
        raise RuntimeError(f"Refusing to interpolate database name {name!r}")
    return f'"{name}"'


def _postgres_unreachable(exc: BaseException) -> None:
    pytest.fail(
        "Postgres test database unreachable. "
        "Run `docker compose up -d postgres` then retry. "
        f"Detail: {exc}"
    )


def _upgrade_database(database_url: str) -> None:
    """Migrate one database. Alembic's env.py reads get_settings(), not the Config URL."""
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    reset_engine()
    sync_url = to_sync_url(database_url)
    probe = create_engine(sync_url, pool_pre_ping=True, connect_args=sync_connect_args())
    try:
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        _postgres_unreachable(exc)
    finally:
        probe.dispose()

    alembic_cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", sync_url)
    try:
        command.upgrade(alembic_cfg, "head")
    finally:
        reset_engine()
        get_settings.cache_clear()


def _clone_from_template(base_url: str, template: str, worker_db: str) -> None:
    admin_url = to_sync_url(
        make_url(base_url).set(database="postgres").render_as_string(hide_password=False)
    )
    admin = create_engine(
        admin_url,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
        connect_args=sync_connect_args(),
    )
    quoted_worker = _quote_db(worker_db)
    quoted_template = _quote_db(template)
    try:
        last_error: Exception | None = None
        for _attempt in range(5):
            try:
                with admin.connect() as conn:
                    conn.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = :datname AND pid <> pg_backend_pid()"
                        ),
                        {"datname": template},
                    )
                    conn.execute(text(f"DROP DATABASE IF EXISTS {quoted_worker} WITH (FORCE)"))
                    conn.execute(
                        text(f"CREATE DATABASE {quoted_worker} TEMPLATE {quoted_template}")
                    )
                return
            except OperationalError as exc:
                _postgres_unreachable(exc)
            except Exception as exc:  # noqa: BLE001 — retry only while the template is busy
                if "is being accessed by other users" not in str(exc):
                    raise
                last_error = exc
                time.sleep(0.2)
        pytest.fail(f"Could not clone {template} into {worker_db}: {last_error}")
    finally:
        admin.dispose()


def _wait_for_worker_clones(ready_dir: Path, worker_count: int) -> None:
    deadline = time.monotonic() + _CLONE_WAIT_SECONDS
    while time.monotonic() < deadline:
        if len(list(ready_dir.glob("gw*"))) >= worker_count:
            return
        time.sleep(0.05)
    ready = sorted(path.name for path in ready_dir.glob("gw*"))
    pytest.fail(
        f"Timed out waiting for {worker_count} xdist workers to clone databases. "
        f"Ready: {ready or 'none'}."
    )


def _activate_worker_database(worker: str) -> None:
    global _WORKER_DATABASE_URL
    if _WORKER_DATABASE_URL is not None:
        return
    base_url = os.environ.get("DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    worker_url = _clone_worker_database(base_url, worker)
    _WORKER_DATABASE_URL = worker_url
    os.environ["DATABASE_URL"] = worker_url
    get_settings.cache_clear()
    reset_engine()


def _clone_worker_database(base_url: str, worker: str) -> str:
    """Migrate the shared test database once, then give this worker its own copy.

    Role DDL in the migrations is cluster-wide, so a per-worker ``alembic upgrade``
    races (``tuple concurrently updated``). A file lock serializes one upgrade of the
    base database; ``CREATE DATABASE ... TEMPLATE`` copies it without running migrations.
    Workers then wait until every clone exists, so no test is connected to the template
    while another worker is still cloning.
    """
    url = make_url(base_url)
    template = url.database
    if template is None:
        raise RuntimeError("DATABASE_URL has no database name")
    _quote_db(template)
    _quote_db(worker)
    worker_db = f"{template}_{worker}"
    _quote_db(worker_db)

    run_id = os.environ.get("PYTEST_XDIST_TESTRUNUID", "local")
    safe_run = re.sub(r"[^A-Za-z0-9_-]", "", run_id) or "local"
    ready_dir = Path("/tmp") / f"curioed-xdist-{safe_run}"
    lock_path = Path("/tmp") / f"curioed-pytest-{template}.lock"
    worker_count = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "1"))

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            _upgrade_database(base_url)
            _clone_from_template(base_url, template, worker_db)
            ready_dir.mkdir(parents=True, exist_ok=True)
            (ready_dir / worker).write_text("ready", encoding="utf-8")
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    _wait_for_worker_clones(ready_dir, worker_count)
    return url.set(database=worker_db).render_as_string(hide_password=False)


def _test_database_url() -> str:
    if _WORKER_DATABASE_URL is not None:
        return _WORKER_DATABASE_URL
    return os.environ.get("DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


@pytest.fixture(scope="session", autouse=True)
def _xdist_worker_database() -> Iterator[None]:
    """One private database per xdist worker. No-op when pytest runs in-process.

    The clone normally happens in ``pytest_collection_finish``. This fixture still
    applies that URL for the session and covers a worker that reached tests without
    one.
    """
    global _WORKER_DATABASE_URL
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker:
        _activate_worker_database(worker)
    yield
    if worker:
        _WORKER_DATABASE_URL = None


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
