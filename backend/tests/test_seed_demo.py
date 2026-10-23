"""The demo seed must not touch a database unless DEMO_SEED=1."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo.py"
_SPEC = importlib.util.spec_from_file_location("seed_demo", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
seed_demo = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(seed_demo)


def test_refuses_without_demo_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEMO_SEED", raising=False)
    with pytest.raises(SystemExit) as caught:
        seed_demo.main()
    assert caught.value.code == 2


def test_seeds_when_flag_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_SEED", "1")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://education:education@127.0.0.1:5432/education",
    )

    class _Result:
        students = 3
        teachers = 1

    calls: list[object] = []

    def _engine(*_args: object, **_kwargs: object) -> object:
        class _Engine:
            def dispose(self) -> None:
                calls.append("disposed")

        return _Engine()

    class _Session:
        def __init__(self, _engine: object) -> None:
            calls.append("session")

        def __enter__(self) -> _Session:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def commit(self) -> None:
            calls.append("commit")

    def _school(session: object, spec: object) -> _Result:
        calls.append(("school", spec))
        return _Result()

    monkeypatch.setattr(seed_demo, "create_engine", _engine)
    monkeypatch.setattr(seed_demo, "Session", _Session)
    monkeypatch.setattr(seed_demo, "generate_school", _school)
    monkeypatch.setattr(
        seed_demo,
        "get_settings",
        lambda: type("Settings", (), {"database_url": "postgresql+asyncpg://unused"})(),
    )
    monkeypatch.setattr(seed_demo, "to_sync_url", lambda url: url)

    seed_demo.main()

    assert "commit" in calls
    assert "disposed" in calls
    assert any(isinstance(item, tuple) and item[0] == "school" for item in calls)
