"""Curriculum seed is a one-off job. API startup does not write it."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import education_platform.main as api_main
from education_platform.core.config import get_settings
from education_platform.db.url import to_sync_url
from education_platform.main import app
from education_platform.modules.academics.constants import POC_INSTITUTION_NAME, POC_TOPIC_SLUG
from education_platform.modules.academics.models import Subtopic, Topic
from education_platform.modules.assessments.models import QuestionAnswerKey
from education_platform.modules.auth.models import Institution, User
from education_platform.modules.materials.seed import main, run_curriculum_seed

_REPO = Path(__file__).resolve().parents[2]
_EMPTY = {
    "institutions": 0,
    "topics": 0,
    "subtopics": 0,
    "answer_keys": 0,
    "users": 0,
}


def _counts(database_url: str) -> dict[str, int]:
    engine = create_engine(to_sync_url(database_url), pool_pre_ping=True)
    with Session(engine) as session:
        counts = {
            "institutions": _count(session, Institution),
            "topics": _count(session, Topic),
            "subtopics": _count(session, Subtopic),
            "answer_keys": _count(session, QuestionAnswerKey),
            "users": _count(session, User),
        }
    engine.dispose()
    return counts


def _count(session: Session, model: type[object]) -> int:
    value = session.scalar(select(func.count()).select_from(model))
    return int(value or 0)


def test_application_module_does_not_call_the_seed() -> None:
    source = Path(api_main.__file__ or "").read_text(encoding="utf-8")
    assert "seed_approved_materials" not in source
    assert "_seed_if_empty" not in source


def test_api_startup_does_not_seed(clean_db: str) -> None:
    assert _counts(clean_db) == _EMPTY

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert _counts(clean_db) == _EMPTY


def test_seed_command_loads_a_fresh_database_and_is_idempotent(
    clean_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _counts(clean_db)["topics"] == 0

    first = run_curriculum_seed()
    assert "rectangles_squares_properties" in first
    assert "square_numbers_patterns" in first
    loaded = _counts(clean_db)
    assert loaded["institutions"] == 1
    assert loaded["topics"] >= 1
    assert loaded["subtopics"] >= 2
    assert loaded["answer_keys"] > 0

    main()
    printed = capsys.readouterr().out
    assert printed.startswith("Seeded topics:")
    assert "rectangles_squares_properties" in printed
    assert _counts(clean_db) == loaded

    second = run_curriculum_seed()
    assert second == first
    assert _counts(clean_db) == loaded

    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        institution = session.scalar(
            select(Institution).where(Institution.name == POC_INSTITUTION_NAME)
        )
        topic = session.scalar(select(Topic).where(Topic.slug == POC_TOPIC_SLUG))
    engine.dispose()
    assert institution is not None
    assert topic is not None
    assert topic.name == "Approved Materials"


def test_seed_command_ignores_seed_synthetic_and_skips_demo_accounts_in_production(
    clean_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEED_SYNTHETIC", "false")
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()

    seeded = run_curriculum_seed()
    assert "rectangles_squares_properties" in seeded

    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        demo = session.scalar(select(User).where(User.email == "student@demo.school"))
        admin = session.scalar(select(User).where(User.email == "admin@demo.school"))
        topic = session.scalar(select(Topic).where(Topic.slug == POC_TOPIC_SLUG))
    engine.dispose()
    assert demo is None
    assert admin is None
    assert topic is not None


def test_compose_prod_runs_the_curriculum_seed_after_migrate() -> None:
    compose = yaml.safe_load((_REPO / "deploy" / "docker-compose.prod.yml").read_text())
    services = compose["services"]
    seed = services["seed"]
    assert seed["restart"] == "no"
    assert seed["entrypoint"] == ["python", "-m", "education_platform.modules.materials.seed"]
    assert seed["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert seed["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert "profiles" not in seed
    assert services["api"]["depends_on"]["seed"]["condition"] == "service_completed_successfully"

    for name in ("deploy.sh", "selfhost.sh"):
        text = (_REPO / "deploy" / name).read_text(encoding="utf-8")
        migrate_at = text.index("--exit-code-from migrate migrate")
        seed_at = text.index("--exit-code-from seed seed")
        api_at = text.index("api worker web caddy")
        assert migrate_at < seed_at < api_at, name
