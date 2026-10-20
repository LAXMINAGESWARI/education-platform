"""Chat completion entry points.

``complete`` and ``complete_sync`` share one profile, one retry policy, and one
usage row shape. Streaming is collected into the same return value.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError

from education_platform.ai_platform.gateway.client import (
    HTTP_REFERER,
    get_async_client,
    get_sync_client,
)
from education_platform.ai_platform.gateway.control import (
    async_semaphore,
    breaker_for,
    sync_semaphore,
)
from education_platform.ai_platform.gateway.registry import StepProfile, resolve
from education_platform.ai_platform.gateway.usage import (
    TokenUsage,
    UsageRecord,
    usage_from_provider,
    write_usage,
)
from education_platform.core.config import Settings, get_settings
from education_platform.core.llm import OpenRouterError

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({408, 429})


@dataclass(frozen=True, slots=True)
class CallContext:
    feature: str
    settings: Settings | None = None


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    parsed_json: dict[str, Any] | None
    usage: TokenUsage
    served_model: str


async def complete(
    step: str,
    messages: Sequence[Mapping[str, Any]],
    *,
    ctx: CallContext,
    response_format: Mapping[str, Any] | None = None,
    stream: bool = False,
) -> Completion:
    cfg = ctx.settings or get_settings()
    profile = _checked_profile(step, ctx, cfg)
    started = time.perf_counter()
    try:
        text, usage, served = await _execute_async(
            profile, messages, cfg, response_format=response_format, stream=stream
        )
    except Exception as exc:
        await _schedule_meter(_error_record(profile, exc, _latency_ms(started)))
        if isinstance(exc, OpenRouterError):
            raise
        raise OpenRouterError(_failure_message(exc)) from exc
    try:
        parsed = _parse_json(text) if response_format is not None else None
    except Exception as exc:
        await _schedule_meter(
            _error_record(profile, exc, _latency_ms(started), usage=usage, served=served)
        )
        if isinstance(exc, OpenRouterError):
            raise
        raise OpenRouterError(_failure_message(exc)) from exc
    await _schedule_meter(_ok_record(profile, usage, served, _latency_ms(started)))
    return Completion(text=text, parsed_json=parsed, usage=usage, served_model=served)


def complete_sync(
    step: str,
    messages: Sequence[Mapping[str, Any]],
    *,
    ctx: CallContext,
    response_format: Mapping[str, Any] | None = None,
    stream: bool = False,
) -> Completion:
    cfg = ctx.settings or get_settings()
    profile = _checked_profile(step, ctx, cfg)
    started = time.perf_counter()
    try:
        text, usage, served = _execute_sync(
            profile, messages, cfg, response_format=response_format, stream=stream
        )
    except Exception as exc:
        _safe_meter(_error_record(profile, exc, _latency_ms(started)))
        if isinstance(exc, OpenRouterError):
            raise
        raise OpenRouterError(_failure_message(exc)) from exc
    try:
        parsed = _parse_json(text) if response_format is not None else None
    except Exception as exc:
        _safe_meter(_error_record(profile, exc, _latency_ms(started), usage=usage, served=served))
        if isinstance(exc, OpenRouterError):
            raise
        raise OpenRouterError(_failure_message(exc)) from exc
    _safe_meter(_ok_record(profile, usage, served, _latency_ms(started)))
    return Completion(text=text, parsed_json=parsed, usage=usage, served_model=served)


async def _execute_async(
    profile: StepProfile,
    messages: Sequence[Mapping[str, Any]],
    settings: Settings,
    *,
    response_format: Mapping[str, Any] | None,
    stream: bool,
) -> tuple[str, TokenUsage, str]:
    breaker = breaker_for(
        profile.step,
        threshold=settings.ai_gateway_circuit_failure_threshold,
        cooldown_seconds=settings.ai_gateway_circuit_cooldown_seconds,
    )
    skipped = not breaker.primary_allowed() and bool(profile.fallback_models)
    using_primary = not skipped
    selected = profile.fallback_models[0] if skipped else profile.model
    semaphore = async_semaphore(
        profile.concurrency_class, _limit(profile.concurrency_class, settings)
    )
    client = get_async_client(settings)
    attempts = profile.max_retries + 1
    deadline = _monotonic() + profile.timeout_seconds
    settled = False
    try:
        for attempt in range(attempts):
            remaining = deadline - _monotonic()
            if remaining <= 0:
                _note_primary(breaker, using_primary=using_primary, outcome="failure")
                settled = True
                raise OpenRouterError("OpenRouter call exceeded its deadline")
            try:
                async with semaphore:
                    text, usage, served = await _once_async(
                        client,
                        profile,
                        messages,
                        selected=selected,
                        skipped_primary=skipped,
                        response_format=response_format,
                        stream=stream,
                        timeout=_attempt_timeout(
                            profile.timeout_seconds,
                            remaining,
                            attempts_after=attempts - attempt - 1,
                        ),
                    )
            except Exception as exc:
                if _can_retry(exc, attempt, attempts, deadline):
                    await _async_sleep(_capped_delay(attempt, exc, settings, deadline))
                    continue
                _note_primary(
                    breaker,
                    using_primary=using_primary,
                    outcome=_outcome_for(exc),
                )
                settled = True
                raise
            _note_primary(breaker, using_primary=using_primary, outcome="success")
            settled = True
            return text, usage, served or selected
        raise OpenRouterError("OpenRouter retry loop exited without a result")
    finally:
        if not settled and using_primary:
            breaker.release_probe()


def _execute_sync(
    profile: StepProfile,
    messages: Sequence[Mapping[str, Any]],
    settings: Settings,
    *,
    response_format: Mapping[str, Any] | None,
    stream: bool,
) -> tuple[str, TokenUsage, str]:
    breaker = breaker_for(
        profile.step,
        threshold=settings.ai_gateway_circuit_failure_threshold,
        cooldown_seconds=settings.ai_gateway_circuit_cooldown_seconds,
    )
    skipped = not breaker.primary_allowed() and bool(profile.fallback_models)
    using_primary = not skipped
    selected = profile.fallback_models[0] if skipped else profile.model
    semaphore = sync_semaphore(
        profile.concurrency_class, _limit(profile.concurrency_class, settings)
    )
    client = get_sync_client(settings)
    attempts = profile.max_retries + 1
    deadline = _monotonic() + profile.timeout_seconds
    settled = False
    try:
        for attempt in range(attempts):
            remaining = deadline - _monotonic()
            if remaining <= 0:
                _note_primary(breaker, using_primary=using_primary, outcome="failure")
                settled = True
                raise OpenRouterError("OpenRouter call exceeded its deadline")
            error: Exception | None = None
            text = ""
            usage = TokenUsage()
            served = ""
            semaphore.acquire()
            try:
                text, usage, served = _once_sync(
                    client,
                    profile,
                    messages,
                    selected=selected,
                    skipped_primary=skipped,
                    response_format=response_format,
                    stream=stream,
                    timeout=_attempt_timeout(
                        profile.timeout_seconds,
                        remaining,
                        attempts_after=attempts - attempt - 1,
                    ),
                )
            except Exception as exc:
                error = exc
            finally:
                semaphore.release()
            if error is None:
                _note_primary(breaker, using_primary=using_primary, outcome="success")
                settled = True
                return text, usage, served or selected
            if _can_retry(error, attempt, attempts, deadline):
                _sync_sleep(_capped_delay(attempt, error, settings, deadline))
                continue
            _note_primary(breaker, using_primary=using_primary, outcome=_outcome_for(error))
            settled = True
            raise error
        raise OpenRouterError("OpenRouter retry loop exited without a result")
    finally:
        if not settled and using_primary:
            breaker.release_probe()


async def _once_async(
    client: Any,
    profile: StepProfile,
    messages: Sequence[Mapping[str, Any]],
    *,
    selected: str,
    skipped_primary: bool,
    response_format: Mapping[str, Any] | None,
    stream: bool,
    timeout: float,
) -> tuple[str, TokenUsage, str]:
    kwargs = _request_kwargs(
        profile,
        messages,
        selected=selected,
        skipped_primary=skipped_primary,
        response_format=response_format,
        stream=stream,
        timeout=timeout,
    )
    create = client.chat.completions.create
    if stream:
        return await _read_async_stream(await create(**kwargs))
    return _read_completion(await create(**kwargs), selected)


def _once_sync(
    client: Any,
    profile: StepProfile,
    messages: Sequence[Mapping[str, Any]],
    *,
    selected: str,
    skipped_primary: bool,
    response_format: Mapping[str, Any] | None,
    stream: bool,
    timeout: float,
) -> tuple[str, TokenUsage, str]:
    kwargs = _request_kwargs(
        profile,
        messages,
        selected=selected,
        skipped_primary=skipped_primary,
        response_format=response_format,
        stream=stream,
        timeout=timeout,
    )
    create = client.chat.completions.create
    if stream:
        return _read_sync_stream(create(**kwargs))
    return _read_completion(create(**kwargs), selected)


def _request_kwargs(
    profile: StepProfile,
    messages: Sequence[Mapping[str, Any]],
    *,
    selected: str,
    skipped_primary: bool,
    response_format: Mapping[str, Any] | None,
    stream: bool,
    timeout: float,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": selected,
        "messages": list(messages),
        "temperature": profile.temperature,
        "timeout": timeout,
        "extra_headers": {"HTTP-Referer": HTTP_REFERER, "X-Title": profile.title},
        "extra_body": {
            "models": _model_chain(profile, selected, skipped_primary=skipped_primary),
            "usage": {"include": True},
        },
    }
    if profile.max_tokens is not None:
        kwargs["max_tokens"] = profile.max_tokens
    if response_format is not None:
        kwargs["response_format"] = dict(response_format)
    if stream:
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
    return kwargs


def _model_chain(profile: StepProfile, selected: str, *, skipped_primary: bool) -> list[str]:
    if skipped_primary:
        ordered = list(profile.fallback_models)
    else:
        ordered = [profile.model, *profile.fallback_models]
    chain: list[str] = []
    for item in ordered:
        if item not in chain:
            chain.append(item)
    if selected not in chain:
        chain.insert(0, selected)
    return chain


def _read_completion(response: Any, selected: str) -> tuple[str, TokenUsage, str]:
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise OpenRouterError("OpenRouter returned no choices")
    content = choices[0].message.content
    if not content:
        raise OpenRouterError("OpenRouter returned an empty completion")
    served = getattr(response, "model", None)
    served_model = served if isinstance(served, str) and served else selected
    return str(content), usage_from_provider(getattr(response, "usage", None)), served_model


async def _read_async_stream(stream: Any) -> tuple[str, TokenUsage, str]:
    parts: list[str] = []
    served = ""
    usage = TokenUsage()
    async for chunk in stream:
        served, usage = _take_chunk(chunk, parts, served, usage)
    if not parts:
        raise OpenRouterError("OpenRouter returned an empty completion")
    return "".join(parts), usage, served


def _read_sync_stream(stream: Any) -> tuple[str, TokenUsage, str]:
    parts: list[str] = []
    served = ""
    usage = TokenUsage()
    for chunk in stream:
        served, usage = _take_chunk(chunk, parts, served, usage)
    if not parts:
        raise OpenRouterError("OpenRouter returned an empty completion")
    return "".join(parts), usage, served


def _take_chunk(
    chunk: Any, parts: list[str], served: str, usage: TokenUsage
) -> tuple[str, TokenUsage]:
    model = getattr(chunk, "model", None)
    if isinstance(model, str) and model:
        served = model
    choices = getattr(chunk, "choices", None) or []
    if choices:
        delta = getattr(choices[0], "delta", None)
        content = getattr(delta, "content", None) if delta is not None else None
        if isinstance(content, str) and content:
            parts.append(content)
    chunk_usage = getattr(chunk, "usage", None)
    if chunk_usage is not None:
        usage = usage_from_provider(chunk_usage)
    return served, usage


def _checked_profile(step: str, ctx: CallContext, settings: Settings) -> StepProfile:
    profile = resolve(step, settings)
    if ctx.feature != profile.feature:
        raise OpenRouterError(f"Step {step} belongs to {profile.feature}, not {ctx.feature}")
    return profile


def _parse_json(text: str) -> dict[str, Any]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise OpenRouterError("Expected a JSON object from OpenRouter")
    return data


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, APITimeoutError | APIConnectionError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in _RETRYABLE_STATUS or exc.status_code >= 500
    return False


def _outcome_for(exc: BaseException) -> str:
    """Retryable provider errors trip the breaker. A 400 is neutral."""
    if _retryable(exc):
        return "failure"
    return "neutral"


def _monotonic() -> float:
    return time.monotonic()


def _attempt_timeout(step_timeout: float, remaining: float, *, attempts_after: int) -> float:
    """Leave half of the deadline for a later try while retries remain.

    A timed-out first attempt would otherwise consume the whole step timeout
    and leave no time for one retry. The last attempt may use what is left.
    """
    if attempts_after > 0:
        if remaining >= step_timeout - 0.05:
            return step_timeout / 2
        return min(step_timeout, remaining / 2)
    if remaining >= step_timeout - 0.05:
        return step_timeout
    return min(step_timeout, remaining)


def _can_retry(exc: BaseException, attempt: int, attempts: int, deadline: float) -> bool:
    return _retryable(exc) and attempt + 1 < attempts and deadline - _monotonic() > 0


def _capped_delay(attempt: int, exc: BaseException, settings: Settings, deadline: float) -> float:
    remaining = deadline - _monotonic()
    if remaining <= 0:
        return 0.0
    return min(_delay(attempt, exc, settings), remaining)


def _note_primary(breaker: Any, *, using_primary: bool, outcome: str) -> None:
    """Record the primary's outcome. Fallback calls leave the cooldown alone."""
    if not using_primary:
        return
    if outcome == "failure":
        breaker.record_failure()
        return
    if outcome == "success":
        breaker.record_success()
        return
    breaker.release_probe()


def _delay(attempt: int, exc: BaseException, settings: Settings) -> float:
    retry_after = _retry_after(exc)
    if retry_after is not None:
        return min(retry_after, settings.ai_gateway_retry_max_seconds)
    growth = float(settings.ai_gateway_retry_base_seconds) * (2**attempt)
    return _jitter(float(min(growth, settings.ai_gateway_retry_max_seconds)))


def _jitter(delay: float) -> float:
    """Spread exponential backoff across half to all of ``delay``."""
    if delay <= 0:
        return 0.0
    return delay * (0.5 + 0.5 * random.random())


def _retry_after(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


def _limit(name: str, settings: Settings) -> int:
    limits = {
        "interactive": settings.ai_gateway_concurrency_interactive,
        "guard": settings.ai_gateway_concurrency_guard,
        "generation": settings.ai_gateway_concurrency_generation,
        "batch": settings.ai_gateway_concurrency_batch,
    }
    try:
        return limits[name]
    except KeyError as exc:
        raise OpenRouterError(f"Unknown concurrency class: {name}") from exc


def _latency_ms(started: float) -> int:
    elapsed = int((time.perf_counter() - started) * 1000)
    return elapsed if elapsed > 0 else 0


def _failure_message(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return f"{type(exc).__name__} (HTTP {status})"
    return type(exc).__name__


def _ok_record(
    profile: StepProfile, usage: TokenUsage, served: str, latency_ms: int
) -> UsageRecord:
    return UsageRecord(
        feature=profile.feature,
        step=profile.step,
        requested_model=profile.model,
        served_model=served,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_tokens=usage.cached_tokens,
        cost_usd=usage.cost_usd,
        latency_ms=latency_ms,
        status="ok",
        error_class=None,
    )


def _error_record(
    profile: StepProfile,
    exc: BaseException,
    latency_ms: int,
    *,
    usage: TokenUsage | None = None,
    served: str | None = None,
) -> UsageRecord:
    tokens = usage or TokenUsage()
    return UsageRecord(
        feature=profile.feature,
        step=profile.step,
        requested_model=profile.model,
        served_model=served,
        prompt_tokens=tokens.prompt_tokens,
        completion_tokens=tokens.completion_tokens,
        cached_tokens=tokens.cached_tokens,
        cost_usd=tokens.cost_usd,
        latency_ms=latency_ms,
        status="error",
        error_class=type(exc).__name__,
    )


def _safe_meter(record: UsageRecord) -> None:
    try:
        write_usage(record)
    except Exception as exc:
        logger.warning(
            "LLM gateway metering failed step=%s error_class=%s",
            record.step,
            type(exc).__name__,
        )


async def _schedule_meter(record: UsageRecord) -> None:
    """Insert usage on a worker thread so the event loop stays free.

    The coroutine waits for that thread. Two completions therefore overlap
    instead of running the sync insert one after another on the loop.
    ``write_usage`` swallows database errors, and an unexpected failure here
    is logged. Neither one fails the completion.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _safe_meter(record)
        return
    try:
        await asyncio.to_thread(_safe_meter, record)
    except Exception as exc:
        logger.warning(
            "LLM gateway metering failed step=%s error_class=%s",
            record.step,
            type(exc).__name__,
        )


def drain_metering(timeout: float = 2.0) -> None:
    """Usage writes finish inside ``complete``. Kept so callers can wait."""
    del timeout


async def _async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _sync_sleep(seconds: float) -> None:
    time.sleep(seconds)
