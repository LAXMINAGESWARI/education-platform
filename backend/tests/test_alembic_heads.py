"""The migration graph must upgrade with a single ``alembic upgrade head``."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import Script, ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from education_platform.db.url import to_sync_url

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# These four revisions joined older branches. The graph from the current head
# back through Jev enforce and the gateway table is a straight line on top of them.
_HISTORICAL_MERGES = frozenset(
    {
        "daf396e7122c",
        "a0b1c2d3e4f5",
        "7eb14ce798c5",
        "a352c5d4a076",
    }
)


def _script() -> ScriptDirectory:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg)


def _parents(revision: Script) -> tuple[str, ...]:
    down = revision.down_revision
    if down is None:
        return ()
    if isinstance(down, tuple):
        return down
    return (down,)


def test_single_head() -> None:
    script = _script()
    heads = script.get_heads()
    assert len(heads) == 1
    assert len(script.get_bases()) == 1

    head_id = heads[0]
    reachable: set[str] = set()
    pending = [head_id]
    while pending:
        revision_id = pending.pop()
        if revision_id in reachable:
            continue
        reachable.add(revision_id)
        revision = script.get_revision(revision_id)
        assert revision is not None
        pending.extend(_parents(revision))

    every_revision = {revision.revision for revision in script.walk_revisions()}
    assert reachable == every_revision

    tip: list[str] = []
    current: Script | None = script.get_revision(head_id)
    while current is not None and isinstance(current.down_revision, str):
        tip.append(current.revision)
        parent_id = current.down_revision
        current = script.get_revision(parent_id)
    assert tip[0] == "b3c4d5e6f7a8"
    assert tip[1] == "d6e7f8a9b0c1"
    assert tip[2] == "c5d6e7f8a9b0"

    merges = {
        revision.revision
        for revision in script.walk_revisions()
        if isinstance(revision.down_revision, tuple)
    }
    assert merges == _HISTORICAL_MERGES


def _alembic_config(sync_url: str) -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg


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
    assert "education_test" in sync_url
    cfg = _alembic_config(sync_url)
    command.upgrade(cfg, "head")
    topics_before = _count(sync_url, "topics")
    assert topics_before > 0
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
        assert _count(sync_url, "topics") == topics_before
    finally:
        command.upgrade(cfg, "head")

    assert "llm_gateway_calls" in _tables(sync_url)
    assert _count(sync_url, "llm_gateway_calls") == 0
    assert _count(sync_url, "topics") == topics_before
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            versions = list(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())
    finally:
        engine.dispose()
    assert versions == list(_script().get_heads())
