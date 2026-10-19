"""judge_evaluations exists, rejects a bad decision, and downgrades cleanly."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from education_platform.db.url import to_sync_url

BACKEND_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.usefixtures("clean_db")

_INSERT = """
INSERT INTO judge_evaluations (
    id, subject_kind, label, mode, decision, reasons, checks, created_at, updated_at
) VALUES (
    gen_random_uuid(), :subject_kind, 'Area', 'shadow', :decision,
    CAST('[]' AS json), CAST('[]' AS json), now(), now()
)
"""


def _config(sync_url: str) -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg


def _table_exists(sync_url: str) -> bool:
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        found = conn.execute(text("SELECT to_regclass('public.judge_evaluations')")).scalar_one()
    engine.dispose()
    return found is not None


def test_check_constraint_rejects_an_unknown_decision(clean_db: str) -> None:
    sync_url = to_sync_url(clean_db)
    assert _table_exists(sync_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text(_INSERT), {"subject_kind": "quiz_item", "decision": "accept"})
        conn.execute(
            text(_INSERT.replace("'shadow'", "'enforce'")),
            {"subject_kind": "lesson", "decision": "needs_review"},
        )
    with engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(text(_INSERT), {"subject_kind": "quiz_item", "decision": "nope"})
    engine.dispose()


def test_downgrade_drops_the_table_and_upgrade_restores_it(clean_db: str) -> None:
    sync_url = to_sync_url(clean_db)
    cfg = _config(sync_url)
    assert _table_exists(sync_url)
    command.downgrade(cfg, "b1c2d3e4f5a6")
    try:
        assert not _table_exists(sync_url)
    finally:
        command.upgrade(cfg, "head")
    assert _table_exists(sync_url)
