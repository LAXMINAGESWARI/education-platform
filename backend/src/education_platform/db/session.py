from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.core.errors import DomainError
from education_platform.db.url import to_async_url, to_sync_url

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_bound_url: str | None = None
_sync_engine: Engine | None = None
_sync_bound_url: str | None = None
_text_to_sql_engine: AsyncEngine | None = None
_text_to_sql_session_factory: async_sessionmaker[AsyncSession] | None = None
_text_to_sql_bound_url: str | None = None


def _pool_options(*, pool_size: int, max_overflow: int) -> dict[str, Any]:
    settings = get_settings()
    return {
        "pool_size": pool_size,
        "max_overflow": max_overflow,
        "pool_timeout": settings.db_pool_timeout_seconds,
        "pool_recycle": settings.db_pool_recycle_seconds,
    }


def _general_pool_options() -> dict[str, Any]:
    settings = get_settings()
    return _pool_options(pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow)


def _text_to_sql_pool_options() -> dict[str, Any]:
    settings = get_settings()
    return _pool_options(
        pool_size=settings.text_to_sql_db_pool_size,
        max_overflow=settings.text_to_sql_db_max_overflow,
    )


def create_general_sync_engine(database_url: str, *, pool_pre_ping: bool = False) -> Engine:
    """Sync engine using the application-role pool settings.

    ``pool_pre_ping`` stays off unless the caller already enabled it.
    """
    options = _general_pool_options()
    if pool_pre_ping:
        options["pool_pre_ping"] = True
    return create_engine(database_url, **options)


def reset_engine() -> None:
    """Dispose cached engines so the next call picks up current settings.

    Tests and process shutdown use this for the main, sync, and text-to-SQL
    engines. Retrieval uses the sync engine, so this is its reset hook too.
    """
    global _engine, _session_factory, _bound_url, _sync_engine, _sync_bound_url
    global _text_to_sql_engine, _text_to_sql_session_factory, _text_to_sql_bound_url
    if _engine is not None:
        # dispose is async; sync dispose via sync_engine for test teardown convenience
        _engine.sync_engine.dispose()
    _engine = None
    _session_factory = None
    _bound_url = None
    if _sync_engine is not None:
        _sync_engine.dispose()
    _sync_engine = None
    _sync_bound_url = None
    if _text_to_sql_engine is not None:
        _text_to_sql_engine.sync_engine.dispose()
    _text_to_sql_engine = None
    _text_to_sql_session_factory = None
    _text_to_sql_bound_url = None


def get_engine() -> AsyncEngine:
    global _engine, _session_factory, _bound_url
    settings = get_settings()
    database_url = to_async_url(settings.database_url)
    if _engine is None or _bound_url != database_url:
        if _engine is not None:
            _engine.sync_engine.dispose()
        _bound_url = database_url
        _engine = create_async_engine(database_url, pool_pre_ping=True, **_general_pool_options())
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def get_sync_engine() -> Engine:
    """Process-wide sync engine for workers (must not create a new engine per poll)."""
    global _sync_engine, _sync_bound_url
    settings = get_settings()
    database_url = to_sync_url(settings.database_url)
    if _sync_engine is None or _sync_bound_url != database_url:
        if _sync_engine is not None:
            _sync_engine.dispose()
        _sync_bound_url = database_url
        _sync_engine = create_general_sync_engine(database_url, pool_pre_ping=True)
    return _sync_engine


def sync_session() -> Session:
    """Return a sync Session bound to the shared worker engine."""
    return Session(get_sync_engine(), expire_on_commit=False)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    session_factory = get_session_factory()
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


def get_text_to_sql_engine() -> AsyncEngine:
    """Process-wide async engine for the text-to-SQL pipeline, authenticated as the
    restricted `text_to_sql_reader` role (see `core.config.Settings.text_to_sql_database_url`
    and migration c9d0e1f2a3b4) rather than the application's own `education` role.
    Cached the same way `get_engine()` caches the main engine, and disposed alongside it
    in `reset_engine()`.
    """
    global _text_to_sql_engine, _text_to_sql_session_factory, _text_to_sql_bound_url
    settings = get_settings()
    database_url = to_async_url(settings.text_to_sql_database_url)
    if _text_to_sql_engine is None or _text_to_sql_bound_url != database_url:
        if _text_to_sql_engine is not None:
            _text_to_sql_engine.sync_engine.dispose()
        _text_to_sql_bound_url = database_url
        _text_to_sql_engine = create_async_engine(
            database_url, pool_pre_ping=True, **_text_to_sql_pool_options()
        )
        _text_to_sql_session_factory = async_sessionmaker(
            _text_to_sql_engine, expire_on_commit=False, class_=AsyncSession
        )
    return _text_to_sql_engine


def get_text_to_sql_session_factory() -> async_sessionmaker[AsyncSession]:
    get_text_to_sql_engine()
    assert _text_to_sql_session_factory is not None
    return _text_to_sql_session_factory
