"""Settings shape. Duplicate fields are how the guard timeout silently became 30s."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from education_platform.core.config import Settings
from education_platform.modules.guard.classifier import DATA_INJECTION
from education_platform.modules.guard.jev_client import JevError, post_decisions

_CONFIG = Path(__file__).resolve().parents[1] / "src" / "education_platform" / "core" / "config.py"


def test_settings_has_no_duplicate_fields() -> None:
    tree = ast.parse(_CONFIG.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Settings":
            continue
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                names.append(statement.target.id)
    assert names
    assert len(names) == len(set(names))
    assert Settings(_env_file=None).jev_guard_timeout_seconds == 5.0


@pytest.mark.asyncio
async def test_post_decisions_never_runs_without_key() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(JevError, match="not configured"):
            await post_decisions(
                state="What subject do I teach?",
                questions={"decision": DATA_INJECTION},
                settings=Settings(_env_file=None, openrouter_api_key=None),
                client=client,
            )
    assert called is False
