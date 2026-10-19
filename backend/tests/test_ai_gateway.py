"""Gateway pooling, retries, metering, and the default-off policy-chat flag."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from education_platform.ai_platform.gateway import api, control
from education_platform.ai_platform.gateway import usage as usage_mod
from education_platform.ai_platform.gateway.api import CallContext, complete, complete_sync
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
from education_platform.ai_platform.gateway.registry import resolve, step_ids
from education_platform.ai_platform.gateway.usage import UsageRecord, write_usage
from education_platform.core.config import Settings
from education_platform.core.llm import _OPENROUTER_HEADERS, OpenRouterError
from education_platform.db.url import to_sync_url
from education_platform.modules.assistant.graph import injection_guard, summarize_node

_PROMPT = "PROMPT-SENTINEL-do-not-store"
_RESPONSE = "RESPONSE-SENTINEL-do-not-store"


@pytest.fixture(autouse=True)
def _reset_gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reset_clients()
    reset_controls()
    bind_http_clients()
    monkeypatch.setattr(api, "write_usage", lambda _record: None)
    yield
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
        "generation.lesson": settings.adk_model,
        "generation.items.writer": settings.adk_model,
        "generation.items.blind_solve": settings.blind_solver_model,
        "generation.vision": settings.openrouter_model,
        "generation.curriculum": settings.adk_model,
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
    assert slept == [0.2, 0.4]
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
    assert slept == [0.2]


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
    assert float(headers["x-stainless-read-timeout"]) == profile.timeout_seconds
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
    assert float(sql_headers["x-stainless-read-timeout"]) == 20.0

    outline = _Script([200], content="outline")
    _bind_async(outline)
    await complete(
        "generation.outline",
        [{"role": "user", "content": "headings"}],
        ctx=_ctx("generation", settings),
    )
    assert float(outline.requests[0].headers["x-stainless-read-timeout"]) == 120.0


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
        with engine.connect() as conn:
            versions = list(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())
        assert versions == ["b3c4d5e6f7a8"]
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
        return {"injection": False, "reason": "ok"}

    async def _forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("gateway must stay unused when the flag is off")

    monkeypatch.setattr(
        "education_platform.modules.assistant.graph.chat_completion_json", _fake_json
    )
    monkeypatch.setattr("education_platform.modules.assistant.graph.complete", _forbidden)
    settings = _settings(ai_gateway_policy_chat=False)
    result = await injection_guard(_guard_state(), settings=settings)
    assert result["injection_blocked"] is False
    assert seen["kwargs"] == {"settings": settings}
    assert seen["messages"][0]["role"] == "system"

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

    monkeypatch.setattr(
        "education_platform.modules.assistant.graph.chat_completion_json", _forbidden
    )
    monkeypatch.setattr("education_platform.modules.assistant.graph.chat_completion", _forbidden)
    guard = _Script([200], content=json.dumps({"injection": False, "reason": "ok"}))
    _bind_async(guard)
    settings = _settings(ai_gateway_features="policy_chat")
    result = await injection_guard(_guard_state(), settings=settings)
    assert result["injection_blocked"] is False
    body = json.loads(guard.requests[0].content.decode())
    assert body["model"] == settings.openrouter_model
    assert body["response_format"] == {"type": "json_object"}
    assert float(guard.requests[0].headers["x-stainless-read-timeout"]) == 8.0

    answer_script = _Script([200], content="## Attendance\n\nNotify the office.")
    _bind_async(answer_script)
    answer = await summarize_node(_guard_state(), settings=settings)
    assert answer["assistant_content"].startswith("## Attendance")
    answer_body = json.loads(answer_script.requests[0].content.decode())
    assert answer_body["temperature"] == 0.2
    assert float(answer_script.requests[0].headers["x-stainless-read-timeout"]) == 30.0
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
    assert float(script.requests[0].headers["x-stainless-read-timeout"]) == 20.0
