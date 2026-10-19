"""Jev stage inside the existing guards. No live OpenRouter calls."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from education_platform.core.config import Settings
from education_platform.modules.assistant.generation_strategy import (
    GUARD_UNAVAILABLE_REPLY,
    INJECTION_BLOCKED_REPLY,
    screen_review_message,
)
from education_platform.modules.assistant.graph import _heuristic_injection
from education_platform.modules.guard.classifier import DATA_INJECTION, guard_state, is_positive
from education_platform.modules.guard.jev_client import JevError, decisions_endpoint, post_decisions

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "education_platform"
    / "modules"
    / "guard"
    / "fixtures"
    / "golden_guard_rows.json"
)


def test_block_threshold_maps_noul_to_the_old_boolean() -> None:
    settings = Settings(jev_block_threshold=0.85)
    assert is_positive(0.85, settings)
    assert is_positive(0.99, settings)
    assert not is_positive(0.84, settings)


def test_golden_rows_keep_the_regex_stage_honest() -> None:
    rows = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    attacks = [row for row in rows if row["task"] == "injection" and row["expect_positive"]]
    ordinary = [row for row in rows if row["task"] == "injection" and not row["expect_positive"]]
    assert len(attacks) >= 8
    assert len(ordinary) >= 5
    # "Disregard your role..." is the classifier-only case from the injection-guard tests.
    classifier_only = next(row for row in attacks if row["id"] == "inj-role")
    assert not _heuristic_injection(classifier_only["text"])
    for row in attacks:
        if row["id"] == "inj-role":
            continue
        assert _heuristic_injection(row["text"]), row["id"]
    for row in ordinary:
        assert not _heuristic_injection(row["text"]), row["id"]
    subject = next(row for row in rows if row["id"] == "on-subject")
    assert subject["expect_positive"] is False
    assert subject["text"] == "What subject do I teach?"


@pytest.mark.asyncio
async def test_review_classifier_blocks_flags_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(openrouter_api_key="test-key", jev_block_threshold=0.85)

    async def high(**kwargs: object) -> float:
        _ = kwargs
        return 0.9

    monkeypatch.setattr(
        "education_platform.modules.assistant.generation_strategy.classify_noul",
        high,
    )
    assert await screen_review_message("Explain this quiz item", (), settings) == (
        INJECTION_BLOCKED_REPLY
    )

    async def low(**kwargs: object) -> float:
        _ = kwargs
        return 0.2

    monkeypatch.setattr(
        "education_platform.modules.assistant.generation_strategy.classify_noul",
        low,
    )
    assert await screen_review_message("Explain this quiz item", (), settings) is None

    async def down(**kwargs: object) -> float:
        _ = kwargs
        raise JevError("upstream down", status_code=402)

    monkeypatch.setattr(
        "education_platform.modules.assistant.generation_strategy.classify_noul",
        down,
    )
    assert await screen_review_message("Explain this quiz item", (), settings) == (
        GUARD_UNAVAILABLE_REPLY
    )


@pytest.mark.asyncio
async def test_decisions_call_posts_model_state_and_questions() -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_base_url="https://openrouter.ai/api/v1",
        jev_model="typesafe/jev-1.14",
    )
    assert decisions_endpoint(settings) == "https://openrouter.ai/api/alpha/decisions"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "model": "typesafe/jev-1.14",
                "answers": {"decision": {"type": "noul", "noul": 0.2}},
                "usage": {"input_tokens": 120, "output_tokens": 0, "cost": 0.000005},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        parsed = await post_decisions(
            state=guard_state("What subject do I teach?"),
            questions={"decision": DATA_INJECTION},
            settings=settings,
            client=client,
        )
    assert parsed.answers["decision"]["noul"] == 0.2
    assert parsed.input_tokens == 120
    body = json.loads(seen[0].content)
    assert body["model"] == "typesafe/jev-1.14"
    assert body["state"]["message"] == "What subject do I teach?"
    assert body["questions"]["decision"]["type"] == "noul"
    assert seen[0].url.path == "/api/alpha/decisions"
    assert "chat/completions" not in str(seen[0].url)


@pytest.mark.asyncio
async def test_jev_retries_once_and_does_not_retry_402() -> None:
    settings = Settings(openrouter_api_key="test-key", jev_model="typesafe/jev-1.14")
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"error": "temporary"})
        return httpx.Response(
            200,
            json={
                "model": "typesafe/jev-1.14",
                "answers": {"decision": {"type": "noul", "noul": 0.1}},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await post_decisions(
            state="hello",
            questions={"decision": {"type": "noul", "instructions": "override?"}},
            settings=settings,
            client=client,
        )
    assert calls["n"] == 2

    calls["n"] = 0

    def payment(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(402, json={"error": {"message": "insufficient credits"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(payment)) as client:
        with pytest.raises(JevError) as caught:
            await post_decisions(
                state="hello",
                questions={"decision": {"type": "noul", "instructions": "override?"}},
                settings=settings,
                client=client,
            )
    assert caught.value.status_code == 402
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_review_classifier_fails_open_when_unconfigured() -> None:
    settings = Settings(openrouter_api_key="")
    assert await screen_review_message("Explain this quiz item", (), settings) is None
