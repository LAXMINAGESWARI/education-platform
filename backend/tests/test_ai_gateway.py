"""Gateway pooling, retries, metering, and the default-off policy-chat flag."""

from __future__ import annotations

import asyncio
import contextlib
import gc
import json
import re
import threading
import time
import weakref
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from education_platform.ai_platform.gateway import api, control
from education_platform.ai_platform.gateway import usage as usage_mod
from education_platform.ai_platform.gateway.api import (
    CallContext,
    complete,
    complete_sync,
    drain_metering,
)
from education_platform.ai_platform.gateway.client import (
    HTTP_REFERER,
    bind_http_clients,
    get_async_client,
    get_sync_client,
    reset_clients,
)
from education_platform.ai_platform.gateway.control import reset_controls
from education_platform.ai_platform.gateway.flags import gateway_enabled
from education_platform.ai_platform.gateway.models import LlmGatewayCall
from education_platform.ai_platform.gateway.registry import (
    MINI_FALLBACKS,
    STRONG_FALLBACKS,
    openrouter_model_id,
    resolve,
    step_ids,
)
from education_platform.ai_platform.gateway.usage import TokenUsage, UsageRecord, write_usage
from education_platform.core.config import Settings, get_settings
from education_platform.core.llm import _OPENROUTER_HEADERS, OpenRouterError
from education_platform.db.url import to_sync_url
from education_platform.modules.assistant.graph import (
    GUARD_UNAVAILABLE_REPLY,
    injection_guard,
    question_validator,
    summarize_node,
)

_PROMPT = "PROMPT-SENTINEL-do-not-store"
_RESPONSE = "RESPONSE-SENTINEL-do-not-store"


@pytest.fixture(autouse=True)
def _reset_gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reset_clients()
    reset_controls()
    bind_http_clients()
    monkeypatch.setattr(api, "write_usage", lambda _record: None)
    yield
    drain_metering()
    bind_http_clients()
    reset_clients()
    reset_controls()


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"openrouter_api_key": "test-key"}
    values.update(overrides)
    return Settings(**values)


def _completion(content: str, *, model: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
            "cost": 0.00021,
            "prompt_tokens_details": {"cached_tokens": 4},
        },
    }


class _Script:
    def __init__(
        self, statuses: list[int], *, content: str = "hello", model: str | None = None
    ) -> None:
        self.statuses = list(statuses)
        self.content = content
        self.model = model
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status = self.statuses.pop(0)
        if status == 200:
            body = json.loads(request.content.decode())
            served = self.model or str(body["model"])
            return httpx.Response(200, json=_completion(self.content, model=served))
        return httpx.Response(
            status, json={"error": {"message": "provider error", "type": "error"}}
        )


def _bind_async(handler: Any) -> None:
    bind_http_clients(async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _bind_sync(handler: Any) -> None:
    bind_http_clients(sync_client=httpx.Client(transport=httpx.MockTransport(handler)))


def _ctx(feature: str, settings: Settings) -> CallContext:
    return CallContext(feature=feature, settings=settings)


def _guard_state(message: str = "What is the late homework policy?") -> dict[str, Any]:
    return {
        "user_message": message,
        "history": [],
        "user_id": str(uuid4()),
        "institution_id": str(uuid4()),
        "user_email": "admin@demo.school",
        "roles": ["administrator"],
        "user_status": "active",
        "injection_blocked": False,
        "question_valid": True,
        "early_reply": None,
        "retrieved_chunks": [],
        "assistant_content": "",
        "citations": [],
        "prompt_tokens": 0,
    }


def test_async_and_sync_clients_are_pooled() -> None:
    settings = _settings()
    async_first = get_async_client(settings)
    sync_first = get_sync_client(settings)
    assert get_async_client(settings) is async_first
    assert get_sync_client(settings) is sync_first
    assert async_first.max_retries == 0
    assert sync_first.max_retries == 0
    assert get_async_client(_settings(openrouter_api_key="other-key")) is not async_first


def test_registry_defaults_match_todays_models() -> None:
    settings = Settings()
    expected_model = {
        "policy_chat.answer": settings.openrouter_model,
        "policy_chat.guard": settings.openrouter_model,
        "policy_chat.validator": settings.openrouter_model,
        "text_to_sql.generate": settings.openrouter_model,
        "text_to_sql.compose": settings.openrouter_model,
        "text_to_sql.guard": settings.openrouter_model,
        "text_to_sql.validator": settings.openrouter_model,
        "text_to_sql.intent": settings.openrouter_model,
        "generation.outline": settings.openrouter_model,
        "generation.lesson": openrouter_model_id(settings.adk_model),
        "generation.items.writer": openrouter_model_id(settings.adk_model),
        "generation.items.blind_solve": settings.blind_solver_model,
        "generation.vision": settings.openrouter_model,
        "generation.curriculum": openrouter_model_id(settings.adk_model),
        "feedback.summary": settings.openrouter_model,
        "review_chat.reply": settings.openrouter_model,
        "authoring.questions": settings.openrouter_model,
    }
    assert set(step_ids()) == set(expected_model)
    for step, model in expected_model.items():
        assert resolve(step, settings).model == model

    answer = resolve("policy_chat.answer", settings)
    guard = resolve("policy_chat.guard", settings)
    sql = resolve("text_to_sql.generate", settings)
    outline = resolve("generation.outline", settings)
    blind = resolve("generation.items.blind_solve", settings)
    feedback = resolve("feedback.summary", settings)
    authoring = resolve("authoring.questions", settings)
    assert answer.temperature == 0.2
    assert guard.temperature == 0.0
    assert feedback.temperature == 0.3
    assert authoring.temperature == 0.4
    assert answer.timeout_seconds == 30.0
    assert sql.timeout_seconds == 20.0
    assert guard.timeout_seconds == 8.0
    assert resolve("text_to_sql.validator", settings).timeout_seconds == 8.0
    assert outline.timeout_seconds == 120.0
    assert feedback.timeout_seconds == 20.0
    assert answer.max_tokens is None
    assert answer.fallback_models
    assert blind.fallback_models
    assert answer.fallback_models != blind.fallback_models
    assert settings.blind_solver_model not in answer.fallback_models
    assert answer.title == _OPENROUTER_HEADERS["X-Title"]
    assert _OPENROUTER_HEADERS["HTTP-Referer"] == HTTP_REFERER

    tuned = Settings(
        openrouter_model="openai/gpt-4.1-mini",
        blind_solver_model="openai/gpt-4.1",
        ai_gateway_timeout_chat_answer_seconds=12,
        ai_gateway_timeout_sql_generate_seconds=9,
        ai_gateway_timeout_guard_seconds=3,
        ai_gateway_timeout_generation_seconds=45,
        ai_gateway_timeout_feedback_seconds=6,
    )
    assert resolve("policy_chat.answer", tuned).model == "openai/gpt-4.1-mini"
    assert resolve("generation.items.blind_solve", tuned).model == "openai/gpt-4.1"
    assert resolve("policy_chat.answer", tuned).timeout_seconds == 12
    assert resolve("text_to_sql.generate", tuned).timeout_seconds == 9
    assert resolve("policy_chat.validator", tuned).timeout_seconds == 3
    assert resolve("generation.lesson", tuned).timeout_seconds == 45
    assert resolve("feedback.summary", tuned).timeout_seconds == 6


def test_feature_flags_default_off_and_are_generic() -> None:
    assert gateway_enabled("policy_chat", Settings()) is False
    assert gateway_enabled("text_to_sql", Settings()) is False
    assert gateway_enabled("generation", Settings()) is False
    assert gateway_enabled("feedback", Settings()) is False
    assert gateway_enabled("policy_chat", Settings(ai_gateway_policy_chat=True)) is True
    assert gateway_enabled("text_to_sql", Settings(ai_gateway_text_to_sql=True)) is True
    assert gateway_enabled("feedback", Settings(ai_gateway_features="feedback, generation")) is True
    assert (
        gateway_enabled("generation", Settings(ai_gateway_features="feedback, generation")) is True
    )
    assert gateway_enabled("policy_chat", Settings(ai_gateway_features="text_to_sql")) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 408])
async def test_retries_backoff_on_provider_status(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(api, "_async_sleep", _sleep)
    script = _Script([status, status, 200], content="recovered")
    _bind_async(script)
    settings = _settings(ai_gateway_max_retries=2, ai_gateway_retry_base_seconds=0.2)
    result = await complete(
        "policy_chat.answer",
        [{"role": "user", "content": _PROMPT}],
        ctx=_ctx("policy_chat", settings),
    )
    assert result.text == "recovered"
    assert len(script.requests) == 3
    assert 0.1 <= slept[0] <= 0.2
    assert 0.2 <= slept[1] <= 0.4
    assert _PROMPT not in str(result)


@pytest.mark.asyncio
async def test_retry_after_header_sets_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(api, "_async_sleep", _sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429,
                headers={"retry-after": "1.5"},
                json={"error": {"message": "slow down", "type": "rate_limit"}},
            )
        body = json.loads(request.content.decode())
        return httpx.Response(200, json=_completion("ok", model=str(body["model"])))

    _bind_async(handler)
    settings = _settings(ai_gateway_max_retries=1)
    result = await complete(
        "policy_chat.guard",
        [{"role": "user", "content": "hi"}],
        ctx=_ctx("policy_chat", settings),
    )
    assert result.text == "ok"
    assert slept == [1.5]


@pytest.mark.asyncio
async def test_retries_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(api, "_async_sleep", _sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused")
        body = json.loads(request.content.decode())
        return httpx.Response(200, json=_completion("ok", model=str(body["model"])))

    _bind_async(handler)
    settings = _settings(ai_gateway_max_retries=1, ai_gateway_retry_base_seconds=0.2)
    result = await complete(
        "policy_chat.answer",
        [{"role": "user", "content": "hello"}],
        ctx=_ctx("policy_chat", settings),
    )
    assert result.text == "ok"
    assert calls["n"] == 2
    assert 0.1 <= slept[0] <= 0.2


@pytest.mark.asyncio
async def test_client_error_is_not_retried() -> None:
    script = _Script([400])
    _bind_async(script)
    settings = _settings(ai_gateway_max_retries=2)
    with pytest.raises(OpenRouterError) as caught:
        await complete(
            "policy_chat.answer",
            [{"role": "user", "content": _PROMPT}],
            ctx=_ctx("policy_chat", settings),
        )
    assert len(script.requests) == 1
    assert _PROMPT not in str(caught.value)
    assert "HTTP 400" in str(caught.value)


@pytest.mark.asyncio
async def test_client_error_does_not_move_the_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(control, "_now", lambda: clock["t"])
    settings = _settings(
        ai_gateway_max_retries=0,
        ai_gateway_circuit_failure_threshold=2,
        ai_gateway_circuit_cooldown_seconds=30,
    )
    primary = resolve("policy_chat.answer", settings).model
    seen: list[str] = []
    statuses = iter([500, 400, 500, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        model = str(body["model"])
        seen.append(model)
        status = next(statuses)
        if status == 200:
            return httpx.Response(200, json=_completion("ok", model=model))
        return httpx.Response(status, json={"error": {"message": "no", "type": "error"}})

    _bind_async(handler)
    for _ in range(3):
        with pytest.raises(OpenRouterError):
            await complete(
                "policy_chat.answer",
                [{"role": "user", "content": "hi"}],
                ctx=_ctx("policy_chat", settings),
            )
    clock["t"] = 1020.0
    cooled = await complete(
        "policy_chat.answer",
        [{"role": "user", "content": "still open"}],
        ctx=_ctx("policy_chat", settings),
    )
    assert seen == [primary, primary, primary, cooled.served_model]
    assert seen[3] != primary
    assert cooled.text == "ok"


@pytest.mark.asyncio
async def test_fallback_models_timeout_and_title_are_sent() -> None:
    script = _Script([200], content="answer", model="google/gemini-2.5-flash")
    _bind_async(script)
    settings = _settings()
    profile = resolve("policy_chat.answer", settings)
    result = await complete(
        "policy_chat.answer",
        [{"role": "user", "content": "question"}],
        ctx=_ctx("policy_chat", settings),
    )
    body = json.loads(script.requests[0].content.decode())
    assert body["model"] == profile.model
    assert body["models"] == [profile.model, *profile.fallback_models]
    assert body["usage"] == {"include": True}
    assert "max_tokens" not in body
    assert body["temperature"] == 0.2
    headers = script.requests[0].headers
    assert headers["x-title"] == "CurioEd Policy Assistant"
    assert headers["http-referer"] == HTTP_REFERER
    assert float(headers["x-stainless-read-timeout"]) == profile.timeout_seconds / 2
    assert result.served_model == "google/gemini-2.5-flash"
    assert result.usage.prompt_tokens == 11
    assert result.usage.completion_tokens == 7
    assert result.usage.cached_tokens == 4
    assert result.usage.cost_usd == Decimal("0.00021")
    assert result.parsed_json is None

    guard = _Script([200], content='{"injection": false}')
    _bind_async(guard)
    await complete(
        "text_to_sql.generate",
        [{"role": "user", "content": "sql"}],
        ctx=_ctx("text_to_sql", settings),
    )
    sql_headers = guard.requests[0].headers
    assert sql_headers["x-title"] == "CurioEd Text to SQL"
    assert float(sql_headers["x-stainless-read-timeout"]) == 10.0

    outline = _Script([200], content="outline")
    _bind_async(outline)
    await complete(
        "generation.outline",
        [{"role": "user", "content": "headings"}],
        ctx=_ctx("generation", settings),
    )
    assert float(outline.requests[0].headers["x-stainless-read-timeout"]) == 60.0


@pytest.mark.asyncio
async def test_circuit_breaker_trips_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(control, "_now", lambda: clock["t"])
    settings = _settings(
        ai_gateway_max_retries=0,
        ai_gateway_circuit_failure_threshold=1,
        ai_gateway_circuit_cooldown_seconds=30,
    )
    profile = resolve("policy_chat.answer", settings)
    primary = profile.model
    fallback = profile.fallback_models[0]
    allow_primary = {"ok": False}
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body)
        if body["model"] == primary and not allow_primary["ok"]:
            return httpx.Response(500, json={"error": {"message": "down", "type": "server"}})
        return httpx.Response(200, json=_completion("served", model=str(body["model"])))

    _bind_async(handler)
    with pytest.raises(OpenRouterError):
        await complete(
            "policy_chat.answer",
            [{"role": "user", "content": _PROMPT}],
            ctx=_ctx("policy_chat", settings),
        )
    fallback_result = await complete(
        "policy_chat.answer",
        [{"role": "user", "content": _PROMPT}],
        ctx=_ctx("policy_chat", settings),
    )
    assert fallback_result.text == "served"
    assert seen[0]["model"] == primary
    assert seen[1]["model"] == fallback
    assert primary not in seen[1]["models"]
    assert seen[1]["models"][0] == fallback

    clock["t"] = 1029.0
    await complete(
        "policy_chat.answer",
        [{"role": "user", "content": "still cooling"}],
        ctx=_ctx("policy_chat", settings),
    )
    assert seen[2]["model"] == fallback

    clock["t"] = 1030.0
    allow_primary["ok"] = True
    recovered = await complete(
        "policy_chat.answer",
        [{"role": "user", "content": "probe"}],
        ctx=_ctx("policy_chat", settings),
    )
    assert seen[3]["model"] == primary
    assert recovered.served_model == primary
    assert _PROMPT not in str(fallback_result)


@pytest.mark.asyncio
async def test_semaphore_caps_concurrency_per_class() -> None:
    class _Overlap(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.current = 0
            self.maximum = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.current += 1
            self.maximum = max(self.maximum, self.current)
            try:
                await asyncio.sleep(0.05)
                body = json.loads(request.content.decode())
                return httpx.Response(200, json=_completion("ok", model=str(body["model"])))
            finally:
                self.current -= 1

    transport = _Overlap()
    settings = _settings(ai_gateway_concurrency_interactive=2)
    bind_http_clients(async_client=httpx.AsyncClient(transport=transport))
    await asyncio.gather(
        *[
            complete(
                "policy_chat.answer",
                [{"role": "user", "content": "go"}],
                ctx=_ctx("policy_chat", settings),
            )
            for _ in range(6)
        ]
    )
    assert transport.maximum == 2

    class _Hold(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            if "HOLD" in request.content.decode():
                self.started.set()
                await self.release.wait()
            return httpx.Response(200, json=_completion("ok", model=str(body["model"])))

    held = _Hold()
    reset_controls()
    reset_clients()
    limited = _settings(ai_gateway_concurrency_interactive=1, ai_gateway_concurrency_guard=1)
    bind_http_clients(async_client=httpx.AsyncClient(transport=held))
    answer = asyncio.create_task(
        complete(
            "policy_chat.answer",
            [{"role": "user", "content": "HOLD"}],
            ctx=_ctx("policy_chat", limited),
        )
    )
    await asyncio.wait_for(held.started.wait(), timeout=2)
    guard = asyncio.create_task(
        complete(
            "policy_chat.guard",
            [{"role": "user", "content": "go"}],
            ctx=_ctx("policy_chat", limited),
        )
    )
    await asyncio.wait_for(guard, timeout=1)
    held.release.set()
    await answer


def test_sync_semaphore_caps_concurrency() -> None:
    class _Overlap(httpx.BaseTransport):
        def __init__(self) -> None:
            self.current = 0
            self.maximum = 0
            self.lock = threading.Lock()

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            with self.lock:
                self.current += 1
                self.maximum = max(self.maximum, self.current)
            try:
                time.sleep(0.05)
                body = json.loads(request.content.decode())
                return httpx.Response(200, json=_completion("ok", model=str(body["model"])))
            finally:
                with self.lock:
                    self.current -= 1

    transport = _Overlap()
    settings = _settings(ai_gateway_concurrency_interactive=1)
    bind_http_clients(sync_client=httpx.Client(transport=transport))
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(
                complete_sync,
                "policy_chat.answer",
                [{"role": "user", "content": "go"}],
                ctx=_ctx("policy_chat", settings),
            )
            for _ in range(4)
        ]
        assert [future.result().text for future in futures] == ["ok", "ok", "ok", "ok"]
    assert transport.maximum == 1
    assert time.perf_counter() - started >= 0.15


@pytest.mark.asyncio
async def test_metering_writes_row_without_prompt_or_response(
    clean_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "write_usage", write_usage)
    script = _Script([200], content=_RESPONSE, model="google/gemini-2.5-flash")
    _bind_async(script)
    settings = _settings()
    result = await complete(
        "policy_chat.answer",
        [{"role": "user", "content": _PROMPT}],
        ctx=_ctx("policy_chat", settings),
    )
    assert result.text == _RESPONSE
    drain_metering()
    engine = create_engine(to_sync_url(clean_db))
    try:
        with Session(engine) as session:
            rows = list(session.scalars(select(LlmGatewayCall)))
        assert len(rows) == 1
        row = rows[0]
        assert row.feature == "policy_chat"
        assert row.step == "policy_chat.answer"
        assert row.requested_model == settings.openrouter_model
        assert row.served_model == "google/gemini-2.5-flash"
        assert row.prompt_tokens == 11
        assert row.completion_tokens == 7
        assert row.cached_tokens == 4
        assert row.cost_usd == Decimal("0.00021")
        assert row.status == "ok"
        assert row.error_class is None
        assert row.latency_ms >= 0
        stored = " ".join(str(getattr(row, column.name)) for column in row.__table__.columns)
        assert _PROMPT not in stored
        assert _RESPONSE not in stored
    finally:
        engine.dispose()


def test_migration_creates_usage_table(clean_db: str) -> None:
    engine = create_engine(to_sync_url(clean_db))
    try:
        tables = set(inspect(engine).get_table_names())
        assert "llm_gateway_calls" in tables
        columns = {column["name"] for column in inspect(engine).get_columns("llm_gateway_calls")}
        assert columns == {
            "id",
            "feature",
            "step",
            "requested_model",
            "served_model",
            "prompt_tokens",
            "completion_tokens",
            "cached_tokens",
            "cost_usd",
            "latency_ms",
            "status",
            "error_class",
            "created_at",
        }
        backend_root = Path(__file__).resolve().parents[1]
        cfg = Config(str(backend_root / "alembic.ini"))
        cfg.set_main_option("script_location", str(backend_root / "alembic"))
        with engine.connect() as conn:
            versions = list(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())
        assert versions == [ScriptDirectory.from_config(cfg).get_current_head()]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_metering_failure_does_not_fail_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_record: UsageRecord) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(api, "write_usage", _boom)
    script = _Script([200], content="still ok")
    _bind_async(script)
    settings = _settings()
    result = await complete(
        "policy_chat.answer",
        [{"role": "user", "content": _PROMPT}],
        ctx=_ctx("policy_chat", settings),
    )
    assert result.text == "still ok"


def test_write_usage_swallows_database_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _explode() -> None:
        raise RuntimeError("no database")

    monkeypatch.setattr(usage_mod, "sync_session", _explode)
    write_usage(
        UsageRecord(
            feature="policy_chat",
            step="policy_chat.answer",
            requested_model="openai/gpt-4o-mini",
            served_model=None,
            prompt_tokens=0,
            completion_tokens=0,
            cached_tokens=0,
            cost_usd=None,
            latency_ms=0,
            status="error",
            error_class="RuntimeError",
        )
    )


@pytest.mark.asyncio
async def test_flag_off_policy_chat_keeps_the_legacy_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def _fake_json(messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        seen["messages"] = messages
        seen["kwargs"] = kwargs
        return {"valid": True, "reason": "ok"}

    async def _forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("gateway must stay unused when the flag is off")

    async def _noul(**_kwargs: Any) -> float:
        seen["jev"] = True
        return 0.0

    monkeypatch.setattr(
        "education_platform.modules.assistant.graph.chat_completion_json", _fake_json
    )
    monkeypatch.setattr("education_platform.modules.assistant.graph.complete", _forbidden)
    monkeypatch.setattr("education_platform.modules.assistant.graph.classify_noul", _noul)
    settings = _settings(ai_gateway_policy_chat=False)
    result = await question_validator(_guard_state(), settings=settings)
    assert result["question_valid"] is True
    assert seen["kwargs"] == {"settings": settings}
    assert seen["messages"][0]["role"] == "system"

    seen.pop("messages")
    injection = await injection_guard(_guard_state(), settings=settings)
    assert injection["injection_blocked"] is False
    assert seen["jev"] is True
    assert "messages" not in seen

    async def _fake_chat(messages: list[dict[str, str]], **kwargs: Any) -> str:
        seen["answer_kwargs"] = kwargs
        seen["answer_messages"] = messages
        return "## Late homework\n\nDue tomorrow."

    monkeypatch.setattr("education_platform.modules.assistant.graph.chat_completion", _fake_chat)
    answer = await summarize_node(_guard_state(), settings=settings)
    assert answer["assistant_content"].startswith("## Late homework")
    assert seen["answer_kwargs"] == {"settings": settings, "temperature": 0.2}


@pytest.mark.asyncio
async def test_flag_on_policy_chat_uses_the_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("legacy client must stay unused when the flag is on")

    async def _noul(**_kwargs: Any) -> float:
        return 0.0

    monkeypatch.setattr(
        "education_platform.modules.assistant.graph.chat_completion_json", _forbidden
    )
    monkeypatch.setattr("education_platform.modules.assistant.graph.chat_completion", _forbidden)
    monkeypatch.setattr("education_platform.modules.assistant.graph.classify_noul", _noul)
    validator = _Script([200], content=json.dumps({"valid": True, "reason": "ok"}))
    _bind_async(validator)
    settings = _settings(ai_gateway_features="policy_chat")
    injection = await injection_guard(_guard_state(), settings=settings)
    assert injection["injection_blocked"] is False
    assert validator.requests == []
    result = await question_validator(_guard_state(), settings=settings)
    assert result["question_valid"] is True
    body = json.loads(validator.requests[0].content.decode())
    assert body["model"] == settings.openrouter_model
    assert body["response_format"] == {"type": "json_object"}
    assert float(validator.requests[0].headers["x-stainless-read-timeout"]) == 4.0

    answer_script = _Script([200], content="## Attendance\n\nNotify the office.")
    _bind_async(answer_script)
    answer = await summarize_node(_guard_state(), settings=settings)
    assert answer["assistant_content"].startswith("## Attendance")
    answer_body = json.loads(answer_script.requests[0].content.decode())
    assert answer_body["temperature"] == 0.2
    assert float(answer_script.requests[0].headers["x-stainless-read-timeout"]) == 15.0
    assert answer_script.requests[0].headers["x-title"] == "CurioEd Policy Assistant"


@pytest.mark.asyncio
async def test_json_response_is_parsed() -> None:
    script = _Script([200], content='{"valid": false, "reason": "off topic"}')
    _bind_async(script)
    settings = _settings()
    result = await complete(
        "policy_chat.validator",
        [{"role": "user", "content": "hello"}],
        ctx=_ctx("policy_chat", settings),
        response_format={"type": "json_object"},
    )
    assert result.parsed_json == {"valid": False, "reason": "off topic"}


def test_sync_completion_uses_the_shared_client() -> None:
    script = _Script([200], content="sync-ok")
    _bind_sync(script)
    settings = _settings()
    first = get_sync_client(settings)
    result = complete_sync(
        "feedback.summary",
        [{"role": "user", "content": "facts"}],
        ctx=_ctx("feedback", settings),
    )
    assert get_sync_client(settings) is first
    assert result.text == "sync-ok"
    assert result.parsed_json is None
    body = json.loads(script.requests[0].content.decode())
    assert body["model"] == settings.openrouter_model
    assert float(script.requests[0].headers["x-stainless-read-timeout"]) == 10.0


def _admin_headers(client: TestClient) -> dict[str, str]:
    settings = get_settings()
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": settings.demo_admin_email,
            "password": settings.demo_admin_password,
            "institution_name": "POC Demo School",
        },
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


class _PolicyTurn:
    """Validator and answer chat completions. Injection stays on Jev Decisions."""

    def __init__(self, *, answer: str) -> None:
        self.answer = answer
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content.decode())
        blob = json.dumps(body["messages"])
        if "security classifier" in blob:
            content = json.dumps({"injection": False, "reason": "ok"})
        elif "valid question" in blob:
            content = json.dumps({"valid": True, "reason": "ok"})
        else:
            content = self.answer
        return httpx.Response(200, json=_completion(content, model=str(body["model"])))


@pytest.mark.regression
@pytest.mark.asyncio
async def test_policy_chat_api_flag_on_meters_validator_and_answer(
    client: TestClient, clean_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "write_usage", write_usage)

    async def _noul(**_kwargs: Any) -> float:
        return 0.0

    monkeypatch.setattr("education_platform.modules.assistant.graph.classify_noul", _noul)
    question = "What is the late homework policy for grade six?"
    answer = "## Late homework\n\nSubmit the work on the next school day."
    script = _PolicyTurn(answer=answer)
    _bind_async(script)
    flagged = get_settings().model_copy(
        update={"openrouter_api_key": "test-key", "ai_gateway_policy_chat": True}
    )
    monkeypatch.setattr(
        "education_platform.modules.assistant.graph.get_settings",
        lambda: flagged,
    )

    async def _empty_retrieve(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "retrieved_chunks": []}

    monkeypatch.setattr(
        "education_platform.modules.assistant.graph.retrieve_node",
        _empty_retrieve,
    )
    headers = _admin_headers(client)
    created = client.post("/api/v1/chats", json={"title": "Homework"}, headers=headers)
    assert created.status_code == 201, created.text
    posted = client.post(
        f"/api/v1/chats/{created.json()['id']}/messages",
        json={"content": question},
        headers=headers,
    )
    assert posted.status_code == 200, posted.text
    assert posted.json()["assistant_message"]["content"] == answer
    assert len(script.requests) == 2
    drain_metering()

    engine = create_engine(to_sync_url(clean_db))
    try:
        with Session(engine) as session:
            rows = list(session.scalars(select(LlmGatewayCall).order_by(LlmGatewayCall.created_at)))
        assert {row.step for row in rows} == {
            "policy_chat.validator",
            "policy_chat.answer",
        }
        assert len(rows) == 2
        assert {row.status for row in rows} == {"ok"}
        stored = " ".join(
            str(getattr(row, column.name)) for row in rows for column in row.__table__.columns
        )
        assert question not in stored
        assert answer not in stored
        assert "security classifier" not in stored
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_flag_on_validator_non_json_fails_closed(
    clean_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "write_usage", write_usage)
    script = _Script([200], content="this is not json")
    _bind_async(script)
    settings = _settings(ai_gateway_policy_chat=True)
    message = "What is the late homework policy?"
    result = await question_validator(_guard_state(message), settings=settings)
    assert result["question_valid"] is False
    assert result["early_reply"] == GUARD_UNAVAILABLE_REPLY
    assert "this is not json" not in (result["early_reply"] or "")
    drain_metering()

    engine = create_engine(to_sync_url(clean_db))
    try:
        with Session(engine) as session:
            rows = list(session.scalars(select(LlmGatewayCall)))
        assert len(rows) == 1
        row = rows[0]
        assert row.step == "policy_chat.validator"
        assert row.status == "error"
        assert row.prompt_tokens == 11
        assert row.completion_tokens == 7
        assert row.cached_tokens == 4
        assert row.served_model == settings.openrouter_model
        stored = " ".join(str(getattr(row, column.name)) for column in row.__table__.columns)
        assert message not in stored
        assert "this is not json" not in stored
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_async_metering_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans: list[tuple[float, float]] = []
    span_lock = threading.Lock()

    def slow(_record: UsageRecord) -> None:
        started_at = time.perf_counter()
        time.sleep(0.5)
        finished_at = time.perf_counter()
        with span_lock:
            spans.append((started_at, finished_at))

    monkeypatch.setattr(api, "write_usage", slow)
    script = _Script([200, 200, 200], content="ok")
    _bind_async(script)
    settings = _settings()

    async def _one() -> str:
        result = await complete(
            "policy_chat.answer",
            [{"role": "user", "content": "hi"}],
            ctx=_ctx("policy_chat", settings),
        )
        return result.text

    await _one()
    spans.clear()
    started = time.perf_counter()
    texts = await asyncio.gather(_one(), _one())
    elapsed = time.perf_counter() - started
    assert texts == ["ok", "ok"]
    assert len(spans) == 2
    first, second = spans
    assert first[0] < second[1] and second[0] < first[1]
    assert elapsed < 0.9


def test_flags_parse_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_FEATURES", " Policy_Chat ,text_to_sql")
    for name in (
        "AI_GATEWAY_POLICY_CHAT",
        "AI_GATEWAY_TEXT_TO_SQL",
        "AI_GATEWAY_GENERATION",
        "AI_GATEWAY_FEEDBACK",
        "AI_GATEWAY_REVIEW_CHAT",
        "AI_GATEWAY_AUTHORING",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert gateway_enabled("policy_chat", settings) is True
    assert gateway_enabled("POLICY_CHAT", settings) is True
    assert gateway_enabled("text_to_sql", settings) is True
    assert gateway_enabled("generation", settings) is False
    assert gateway_enabled(" feedback ", settings) is False


def test_backoff_jitter_is_half_to_full_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(ai_gateway_retry_base_seconds=0.2, ai_gateway_retry_max_seconds=8)
    draws = iter((0.0, 1.0))
    monkeypatch.setattr(api.random, "random", lambda: next(draws))
    assert api._delay(0, TimeoutError(), settings) == 0.1
    assert api._delay(1, TimeoutError(), settings) == 0.4


@pytest.mark.asyncio
async def test_step_deadline_bounds_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr(api, "_monotonic", lambda: clock["t"])
    timeouts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(float(request.headers["x-stainless-read-timeout"]))
        clock["t"] += 12
        return httpx.Response(500, json={"error": {"message": "down", "type": "server"}})

    _bind_async(handler)
    settings = _settings(
        ai_gateway_max_retries=8,
        ai_gateway_timeout_chat_answer_seconds=30,
        ai_gateway_retry_base_seconds=0,
    )
    with pytest.raises(OpenRouterError):
        await complete(
            "policy_chat.answer",
            [{"role": "user", "content": "hi"}],
            ctx=_ctx("policy_chat", settings),
        )
    assert timeouts == [15.0, 9.0, 3.0]

    bounded = _settings(ai_gateway_max_retries=4, ai_gateway_retry_base_seconds=0)
    for step, limit in (
        ("policy_chat.guard", 8.0),
        ("policy_chat.validator", 8.0),
        ("policy_chat.answer", 30.0),
    ):
        clock["t"] = 0.0
        seen: list[float] = []

        def slow_guard(request: httpx.Request, bucket: list[float] = seen) -> httpx.Response:
            timeout = float(request.headers["x-stainless-read-timeout"])
            bucket.append(timeout)
            clock["t"] += timeout
            return httpx.Response(500, json={"error": {"message": "down", "type": "server"}})

        _bind_async(slow_guard)
        with pytest.raises(OpenRouterError):
            await complete(
                step,
                [{"role": "user", "content": "hi"}],
                ctx=_ctx("policy_chat", bounded),
            )
        assert len(seen) >= 2
        assert sum(seen) <= limit + 1e-6


@pytest.mark.asyncio
async def test_fallback_failure_does_not_extend_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(control, "_now", lambda: clock["t"])
    settings = _settings(
        ai_gateway_max_retries=0,
        ai_gateway_circuit_failure_threshold=1,
        ai_gateway_circuit_cooldown_seconds=30,
    )
    primary = resolve("policy_chat.answer", settings).model
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(str(body["model"]))
        if body["model"] == primary and len(seen) > 1:
            return httpx.Response(200, json=_completion("back", model=primary))
        return httpx.Response(500, json={"error": {"message": "down", "type": "server"}})

    _bind_async(handler)
    with pytest.raises(OpenRouterError):
        await complete(
            "policy_chat.answer",
            [{"role": "user", "content": "open"}],
            ctx=_ctx("policy_chat", settings),
        )
    clock["t"] = 1020.0
    with pytest.raises(OpenRouterError):
        await complete(
            "policy_chat.answer",
            [{"role": "user", "content": "fallback fails"}],
            ctx=_ctx("policy_chat", settings),
        )
    clock["t"] = 1030.0
    recovered = await complete(
        "policy_chat.answer",
        [{"role": "user", "content": "probe"}],
        ctx=_ctx("policy_chat", settings),
    )
    assert seen[0] == primary
    assert seen[1] != primary
    assert seen[2] == primary
    assert recovered.text == "back"


@pytest.mark.asyncio
async def test_half_open_probe_failure_reopens_for_one_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(control, "_now", lambda: clock["t"])
    settings = _settings(
        ai_gateway_max_retries=0,
        ai_gateway_circuit_failure_threshold=3,
        ai_gateway_circuit_cooldown_seconds=30,
    )
    primary = resolve("policy_chat.answer", settings).model
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        model = str(body["model"])
        seen.append(model)
        if model == primary:
            return httpx.Response(500, json={"error": {"message": "down", "type": "server"}})
        return httpx.Response(200, json=_completion("fb", model=model))

    _bind_async(handler)
    for _ in range(3):
        with pytest.raises(OpenRouterError):
            await complete(
                "policy_chat.answer",
                [{"role": "user", "content": "trip"}],
                ctx=_ctx("policy_chat", settings),
            )
    clock["t"] = 1030.0
    with pytest.raises(OpenRouterError):
        await complete(
            "policy_chat.answer",
            [{"role": "user", "content": "probe fails"}],
            ctx=_ctx("policy_chat", settings),
        )
    clock["t"] = 1059.0
    cooled = await complete(
        "policy_chat.answer",
        [{"role": "user", "content": "still open"}],
        ctx=_ctx("policy_chat", settings),
    )
    assert cooled.text == "fb"
    assert seen[-1] != primary

    clock["t"] = 1060.0
    release = asyncio.Event()
    entered = asyncio.Event()
    models: list[str] = []

    class _Gate(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            model = str(body["model"])
            models.append(model)
            if model == primary:
                entered.set()
                await release.wait()
            return httpx.Response(200, json=_completion("ok", model=model))

    reset_clients()
    bind_http_clients(async_client=httpx.AsyncClient(transport=_Gate()))
    probe = asyncio.create_task(
        complete(
            "policy_chat.answer",
            [{"role": "user", "content": "one probe"}],
            ctx=_ctx("policy_chat", settings),
        )
    )
    await entered.wait()
    other = asyncio.create_task(
        complete(
            "policy_chat.answer",
            [{"role": "user", "content": "not the probe"}],
            ctx=_ctx("policy_chat", settings),
        )
    )
    await other
    release.set()
    await probe
    assert models.count(primary) == 1
    assert any(model != primary for model in models)


@pytest.mark.asyncio
async def test_cancelled_half_open_probe_releases_the_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(control, "_now", lambda: clock["t"])
    settings = _settings(
        ai_gateway_max_retries=0,
        ai_gateway_circuit_failure_threshold=1,
        ai_gateway_circuit_cooldown_seconds=30,
    )
    primary = resolve("policy_chat.answer", settings).model
    script = _Script([500])
    _bind_async(script)
    with pytest.raises(OpenRouterError):
        await complete(
            "policy_chat.answer",
            [{"role": "user", "content": "trip"}],
            ctx=_ctx("policy_chat", settings),
        )
    clock["t"] = 1030.0
    started = asyncio.Event()

    async def _hang(*_args: Any, **_kwargs: Any) -> tuple[str, Any, str]:
        started.set()
        await asyncio.Future()
        raise AssertionError("cancelled probe must not finish")

    monkeypatch.setattr(api, "_once_async", _hang)
    probe = asyncio.create_task(
        complete(
            "policy_chat.answer",
            [{"role": "user", "content": "probe"}],
            ctx=_ctx("policy_chat", settings),
        )
    )
    await started.wait()
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe

    chosen: list[str] = []

    async def _ok(*_args: Any, **kwargs: Any) -> tuple[str, Any, str]:
        chosen.append(str(kwargs["selected"]))
        return ("again", api.TokenUsage(), str(kwargs["selected"]))

    monkeypatch.setattr(api, "_once_async", _ok)
    result = await complete(
        "policy_chat.answer",
        [{"role": "user", "content": "next"}],
        ctx=_ctx("policy_chat", settings),
    )
    assert chosen == [primary]
    assert result.text == "again"


@pytest.mark.asyncio
async def test_half_open_probe_client_error_releases_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(control, "_now", lambda: clock["t"])
    settings = _settings(
        ai_gateway_max_retries=0,
        ai_gateway_circuit_failure_threshold=1,
        ai_gateway_circuit_cooldown_seconds=30,
    )
    primary = resolve("policy_chat.answer", settings).model
    seen: list[str] = []
    statuses = iter((500, 400, 200))

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        model = str(body["model"])
        seen.append(model)
        status = next(statuses)
        if status == 200:
            return httpx.Response(200, json=_completion("back", model=model))
        return httpx.Response(status, json={"error": {"message": "bad request", "type": "error"}})

    _bind_async(handler)
    with pytest.raises(OpenRouterError):
        await complete(
            "policy_chat.answer",
            [{"role": "user", "content": "trip"}],
            ctx=_ctx("policy_chat", settings),
        )
    clock["t"] = 1030.0
    with pytest.raises(OpenRouterError):
        await complete(
            "policy_chat.answer",
            [{"role": "user", "content": "probe"}],
            ctx=_ctx("policy_chat", settings),
        )
    again = await complete(
        "policy_chat.answer",
        [{"role": "user", "content": "next"}],
        ctx=_ctx("policy_chat", settings),
    )
    assert seen == [primary, primary, primary]
    assert again.text == "back"


class _ProbeInterrupted(BaseException):
    """Stands in for cancellation on the sync path."""


def test_sync_half_open_probe_base_exception_releases_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(control, "_now", lambda: clock["t"])
    settings = _settings(
        ai_gateway_max_retries=0,
        ai_gateway_circuit_failure_threshold=1,
        ai_gateway_circuit_cooldown_seconds=30,
    )
    primary = resolve("policy_chat.answer", settings).model
    _bind_sync(_Script([500]))
    with pytest.raises(OpenRouterError):
        complete_sync(
            "policy_chat.answer",
            [{"role": "user", "content": "trip"}],
            ctx=_ctx("policy_chat", settings),
        )
    clock["t"] = 1030.0

    def _interrupt(*_args: Any, **_kwargs: Any) -> tuple[str, TokenUsage, str]:
        raise _ProbeInterrupted()

    monkeypatch.setattr(api, "_once_sync", _interrupt)
    with pytest.raises(_ProbeInterrupted):
        complete_sync(
            "policy_chat.answer",
            [{"role": "user", "content": "probe"}],
            ctx=_ctx("policy_chat", settings),
        )
    chosen: list[str] = []

    def _ok(*_args: Any, **kwargs: Any) -> tuple[str, TokenUsage, str]:
        selected = str(kwargs["selected"])
        chosen.append(selected)
        return ("again", TokenUsage(), selected)

    monkeypatch.setattr(api, "_once_sync", _ok)
    result = complete_sync(
        "policy_chat.answer",
        [{"role": "user", "content": "next"}],
        ctx=_ctx("policy_chat", settings),
    )
    assert chosen == [primary]
    assert result.text == "again"


def test_sync_non_json_records_token_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    records: list[UsageRecord] = []
    monkeypatch.setattr(api, "write_usage", records.append)
    script = _Script([200], content="not json", model="openai/gpt-4o-mini")
    _bind_sync(script)
    with pytest.raises(OpenRouterError):
        complete_sync(
            "policy_chat.validator",
            [{"role": "user", "content": "hello"}],
            ctx=_ctx("policy_chat", _settings()),
            response_format={"type": "json_object"},
        )
    assert len(records) == 1
    record = records[0]
    assert record.status == "error"
    assert record.prompt_tokens == 11
    assert record.completion_tokens == 7
    assert record.cached_tokens == 4
    assert record.cost_usd == Decimal("0.00021")
    assert record.served_model == "openai/gpt-4o-mini"


def test_generation_steps_strip_the_litellm_prefix() -> None:
    settings = _settings(adk_model="openrouter/openai/gpt-4o-mini")
    for step in ("generation.lesson", "generation.items.writer", "generation.curriculum"):
        assert resolve(step, settings).model == "openai/gpt-4o-mini"
    assert resolve("generation.outline", settings).model == settings.openrouter_model
    assert not resolve("generation.lesson", settings).model.startswith("openrouter/")


_MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9.-]*/[A-Za-z0-9][A-Za-z0-9._:-]*$")


def test_fallback_model_ids_are_well_formed() -> None:
    ids = (*MINI_FALLBACKS, *STRONG_FALLBACKS)
    assert len(ids) == 4
    assert len(set(ids)) == 4
    for model_id in ids:
        assert _MODEL_ID.fullmatch(model_id)
        assert not model_id.endswith(":batch")


def test_async_client_and_semaphore_follow_each_event_loop() -> None:
    settings = _settings()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        return httpx.Response(200, json=_completion("ok", model=str(body["model"])))

    bind_http_clients(async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    def once() -> str:
        async def inner() -> str:
            result = await complete(
                "policy_chat.answer",
                [{"role": "user", "content": "hi"}],
                ctx=_ctx("policy_chat", settings),
            )
            control.async_semaphore("interactive", 4)
            return result.text

        return asyncio.run(inner())

    assert once() == "ok"
    assert once() == "ok"
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            once()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_contended_semaphore_does_not_keep_the_loop_alive() -> None:
    loop = asyncio.new_event_loop()
    ref = weakref.ref(loop)

    async def _contend() -> None:
        semaphore = control.async_semaphore("gc-probe", 1)
        await semaphore.acquire()

        async def _blocked() -> None:
            await semaphore.acquire()

        waiter = asyncio.create_task(_blocked())
        await asyncio.sleep(0)
        waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiter
        semaphore.release()

    loop.run_until_complete(_contend())
    loop.close()
    del loop
    gc.collect()
    assert ref() is None


def test_sync_semaphore_is_released_when_the_call_raises() -> None:
    settings = _settings(ai_gateway_concurrency_interactive=1, ai_gateway_max_retries=0)
    _bind_sync(_Script([500]))
    with pytest.raises(OpenRouterError):
        complete_sync(
            "policy_chat.answer",
            [{"role": "user", "content": "fail"}],
            ctx=_ctx("policy_chat", settings),
        )
    _bind_sync(_Script([200], content="after"))
    done = threading.Event()
    box: dict[str, str] = {}

    def second() -> None:
        try:
            box["text"] = complete_sync(
                "policy_chat.answer",
                [{"role": "user", "content": "again"}],
                ctx=_ctx("policy_chat", settings),
            ).text
        finally:
            done.set()

    thread = threading.Thread(target=second)
    thread.start()
    assert done.wait(2)
    thread.join(timeout=1)
    assert box["text"] == "after"
