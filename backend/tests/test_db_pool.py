"""SQLAlchemy pool sizes come from settings and are shared by retrieval."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.pool import Pool

from education_platform.core.config import Settings, get_settings
from education_platform.db.session import (
    create_general_sync_engine,
    get_engine,
    get_sync_engine,
    get_text_to_sql_engine,
    reset_engine,
)
from education_platform.main import app, lifespan
from education_platform.modules.assistant.tools.retrieve_chunks import _load_chunk_payloads
from education_platform.modules.judge.bank_load import _session
from education_platform.modules.materials import seed as seed_mod
from education_platform.modules.rag.vector_store import SimilarChunk
from education_platform.modules.synthetic import cli as cli_mod
from education_platform.modules.synthetic import promote as promote_mod
from education_platform.modules.synthetic.generator import GenerationResult
from education_platform.modules.synthetic.promote import PromoteResult

_POOL_ENV = {
    "DB_POOL_SIZE": "4",
    "DB_MAX_OVERFLOW": "3",
    "DB_POOL_TIMEOUT_SECONDS": "11",
    "DB_POOL_RECYCLE_SECONDS": "120",
    "TEXT_TO_SQL_DB_POOL_SIZE": "2",
    "TEXT_TO_SQL_DB_MAX_OVERFLOW": "1",
}


def _assert_pool(
    pool: Pool,
    *,
    size: int,
    overflow: int,
    timeout: float,
    recycle: int,
    pre_ping: bool,
) -> None:
    assert pool.size() == size
    assert pool._max_overflow == overflow
    assert pool.timeout() == timeout
    assert pool._recycle == recycle
    assert pool._pre_ping is pre_ping


def _recording_session(created: list[Engine]) -> type[object]:
    class _Session:
        def __init__(self, engine: Engine) -> None:
            created.append(engine)

        def __enter__(self) -> _Session:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def commit(self) -> None:
            return None

    return _Session


@pytest.fixture
def configured_pools(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key, value in _POOL_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    reset_engine()
    yield
    reset_engine()
    get_settings.cache_clear()


def test_pool_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _POOL_ENV:
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.db_pool_size == 5
    assert settings.db_max_overflow == 5
    assert settings.db_pool_timeout_seconds == 30.0
    assert settings.db_pool_recycle_seconds == 1800
    assert settings.text_to_sql_db_pool_size == 5
    assert settings.text_to_sql_db_max_overflow == 5

    get_settings.cache_clear()
    reset_engine()
    engine = create_general_sync_engine(
        "postgresql+psycopg://education:education@localhost/education"
    )
    try:
        _assert_pool(
            engine.pool,
            size=5,
            overflow=5,
            timeout=30.0,
            recycle=1800,
            pre_ping=False,
        )
    finally:
        engine.dispose()
        reset_engine()
        get_settings.cache_clear()


def test_pool_settings_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _POOL_ENV.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)
    assert settings.db_pool_size == 4
    assert settings.db_max_overflow == 3
    assert settings.db_pool_timeout_seconds == 11.0
    assert settings.db_pool_recycle_seconds == 120
    assert settings.text_to_sql_db_pool_size == 2
    assert settings.text_to_sql_db_max_overflow == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("db_pool_size", 0),
        ("db_max_overflow", -1),
        ("text_to_sql_db_pool_size", 0),
        ("text_to_sql_db_max_overflow", -1),
    ],
)
def test_pool_sizes_are_validated(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_cached_engines_use_configured_pools(configured_pools: None) -> None:
    _ = configured_pools
    main = get_engine()
    again = get_engine()
    sync = get_sync_engine()
    reader = get_text_to_sql_engine()
    assert again is main
    assert get_sync_engine() is sync
    assert get_text_to_sql_engine() is reader
    _assert_pool(main.pool, size=4, overflow=3, timeout=11.0, recycle=120, pre_ping=True)
    _assert_pool(sync.pool, size=4, overflow=3, timeout=11.0, recycle=120, pre_ping=True)
    _assert_pool(reader.pool, size=2, overflow=1, timeout=11.0, recycle=120, pre_ping=True)


def test_startup_seed_engine_uses_the_general_pool(
    configured_pools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = configured_pools
    created: list[Engine] = []
    monkeypatch.setattr("education_platform.main.Session", _recording_session(created))
    monkeypatch.setattr("education_platform.main.seed_approved_materials", lambda *_a, **_k: None)
    from education_platform.main import _seed_if_empty

    _seed_if_empty()
    assert len(created) == 1
    _assert_pool(created[0].pool, size=4, overflow=3, timeout=11.0, recycle=120, pre_ping=True)


def test_curriculum_seed_cli_uses_the_general_pool(
    configured_pools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = configured_pools
    created: list[Engine] = []
    monkeypatch.setattr(seed_mod, "Session", _recording_session(created))
    monkeypatch.setattr(seed_mod, "seed_approved_materials", lambda *_a, **_k: [])
    seed_mod.main()
    assert len(created) == 1
    _assert_pool(created[0].pool, size=4, overflow=3, timeout=11.0, recycle=120, pre_ping=False)


def test_bank_load_engine_uses_the_general_pool(configured_pools: None) -> None:
    _ = configured_pools
    session = _session()
    bind = session.get_bind()
    assert isinstance(bind, Engine)
    try:
        _assert_pool(bind.pool, size=4, overflow=3, timeout=11.0, recycle=120, pre_ping=False)
    finally:
        session.close()
        bind.dispose()


def test_synthetic_cli_engine_uses_the_general_pool(
    configured_pools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = configured_pools
    created: list[Engine] = []
    monkeypatch.setattr(cli_mod, "Session", _recording_session(created))
    monkeypatch.setattr(
        cli_mod,
        "generate_school",
        lambda _session, _spec: GenerationResult(institution_id=uuid4()),
    )
    monkeypatch.setattr(sys, "argv", ["synthetic-cli"])
    cli_mod.main()
    assert len(created) == 1
    _assert_pool(created[0].pool, size=4, overflow=3, timeout=11.0, recycle=120, pre_ping=True)


def test_promote_cli_engine_uses_the_general_pool(
    configured_pools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = configured_pools
    created: list[Engine] = []
    monkeypatch.setattr(promote_mod, "Session", _recording_session(created))
    monkeypatch.setattr(promote_mod, "promote_core_topics", lambda _session: PromoteResult())
    promote_mod.main()
    assert len(created) == 1
    _assert_pool(created[0].pool, size=4, overflow=3, timeout=11.0, recycle=120, pre_ping=True)


def test_retrieve_chunks_reuses_one_engine(
    configured_pools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = configured_pools
    from education_platform.db import session as session_mod

    created: list[Engine] = []
    real_create_engine = session_mod.create_engine

    def _counting(url: str, **kwargs: object) -> Engine:
        engine = real_create_engine(url, **kwargs)
        created.append(engine)
        return engine

    monkeypatch.setattr(session_mod, "create_engine", _counting)
    hit = SimilarChunk(
        chunk_id=uuid4(),
        version_id=uuid4(),
        doc_id=uuid4(),
        doc_kind="not-a-stored-kind",
        distance=0.2,
    )
    assert _load_chunk_payloads([]) == []
    assert created == []
    assert _load_chunk_payloads([hit]) == []
    assert _load_chunk_payloads([hit]) == []
    assert len(created) == 1
    assert get_sync_engine() is created[0]
    _assert_pool(created[0].pool, size=4, overflow=3, timeout=11.0, recycle=120, pre_ping=True)


async def test_lifespan_disposes_the_shared_sync_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("education_platform.main._seed_if_empty", lambda: None)
    reset_engine()
    try:
        first = get_sync_engine()
        async with lifespan(app):
            assert get_sync_engine() is first
        assert get_sync_engine() is not first
    finally:
        reset_engine()
