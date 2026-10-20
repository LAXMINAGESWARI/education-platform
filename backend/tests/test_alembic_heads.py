"""The migration graph must upgrade with a single ``alembic upgrade head``."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from education_platform.db.url import to_sync_url

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _script() -> ScriptDirectory:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_alembic_script_has_one_head_and_a_linear_tip() -> None:
    assert len(_script().get_heads()) == 1


def _alembic_config(sync_url: str) -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg


def _table_counts(sync_url: str, *, skip: frozenset[str] = frozenset()) -> dict[str, int]:
    engine = create_engine(sync_url)
    try:
        names = [name for name in inspect(engine).get_table_names() if name not in skip]
        with engine.connect() as conn:
            return {
                name: int(conn.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one())
                for name in names
            }
    finally:
        engine.dispose()


def _count(sync_url: str, table: str) -> int:
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            return int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
    finally:
        engine.dispose()


def _tables(sync_url: str) -> set[str]:
    engine = create_engine(sync_url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_gateway_migration_round_trip_on_seeded_db(seeded_db: Session, clean_db: str) -> None:
    """Upgrade, downgrade, and upgrade again while curriculum rows are present."""
    seeded_db.commit()
    sync_url = to_sync_url(clean_db)
    cfg = _alembic_config(sync_url)
    command.upgrade(cfg, "head")
    before = _table_counts(sync_url, skip=frozenset({"llm_gateway_calls"}))
    assert before["topics"] > 0
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO llm_gateway_calls ("
                    "id, feature, step, requested_model, prompt_tokens, completion_tokens, "
                    "cached_tokens, latency_ms, status"
                    ") VALUES ("
                    ":id, 'policy_chat', 'policy_chat.answer', 'openai/gpt-4o-mini', "
                    "1, 1, 0, 1, 'ok'"
                    ")"
                ),
                {"id": uuid4()},
            )
    finally:
        engine.dispose()
    assert _count(sync_url, "llm_gateway_calls") == 1

    command.downgrade(cfg, "-1")
    try:
        assert "llm_gateway_calls" not in _tables(sync_url)
        assert _table_counts(sync_url) == before
    finally:
        command.upgrade(cfg, "head")

    assert "llm_gateway_calls" in _tables(sync_url)
    assert _count(sync_url, "llm_gateway_calls") == 0
    assert _table_counts(sync_url, skip=frozenset({"llm_gateway_calls"})) == before
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            versions = list(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())
    finally:
        engine.dispose()
    assert versions == list(_script().get_heads())
