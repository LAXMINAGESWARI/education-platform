"""HTTP adapter for Jev on OpenRouter.

OpenRouter calls a decisions model with:

``POST https://openrouter.ai/api/alpha/decisions``

Authorization is ``Bearer <OPENROUTER_API_KEY>``, the same key and attribution
headers as ``education_platform.core.llm``. The JSON body is ``model``
(``JEV_MODEL``, default ``typesafe/jev-1.14``), ``state`` (string, object, or
array), and ``questions`` keyed by caller ids. Each question is ``noul``,
``choice``, or ``score``. The response is ``id``, ``model``, ``provider``,
``answers``, and ``usage`` (``input_tokens``, ``output_tokens``, ``cost``).
That shape is the OpenAPI operation "Submit a Decisions request"
(https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request).

The chat base ``OPENROUTER_BASE_URL`` (``/api/v1``) is a different API and is
not prefixed onto this path.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from education_platform.core.config import Settings, get_settings
from education_platform.core.llm import _OPENROUTER_HEADERS
from education_platform.modules.judge.errors import (
    JudgeError,
    JudgePaymentRequired,
    JudgeRateLimited,
    JudgeResponseError,
)
from education_platform.modules.judge.types import (
    Answer,
    ChoiceAnswer,
    DecisionRequest,
    DecisionResult,
    NoulAnswer,
    ScoreAnswer,
)

OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
INPUT_USD_PER_MILLION = 0.042
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 529})


def _default_sleep(seconds: float) -> None:
    time.sleep(seconds)


def estimate_cost_usd(input_tokens: int) -> float:
    """Jev output tokens are free. Input is priced per million."""
    if input_tokens < 0:
        return 0.0
    return input_tokens * INPUT_USD_PER_MILLION / 1_000_000


class HttpJudge:
    """Decisions client with a 30s timeout and backoff on rate limits."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = _default_sleep,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._sleep = sleep
        self._clock = clock

    def decide(self, request: DecisionRequest) -> DecisionResult:
        settings = self._settings
        if not settings.openrouter_configured:
            raise JudgeError(
                "OPENROUTER_API_KEY is not configured. Set it in backend/.env to score with Jev."
            )
        api_key = (settings.openrouter_api_key or "").strip()
        payload = request.to_api(model=settings.jev_model)
        headers = dict(_OPENROUTER_HEADERS)
        headers["Authorization"] = f"Bearer {api_key}"
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=settings.jev_timeout_seconds)
        attempts = max(0, settings.jev_max_retries) + 1
        try:
            return self._send(client, OPENROUTER_DECISIONS_URL, payload, headers, attempts)
        finally:
            if owns_client:
                client.close()

    def _send(
        self,
        client: httpx.Client,
        url: str,
        payload: dict[str, object],
        headers: dict[str, str],
        attempts: int,
    ) -> DecisionResult:
        last_status: int | None = None
        for attempt in range(attempts):
            started = self._clock()
            try:
                response = client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                if attempt + 1 >= attempts:
                    raise JudgeError("Jev request timed out.") from exc
                self._sleep(_backoff(attempt))
                continue
            except httpx.HTTPError as exc:
                if attempt + 1 >= attempts:
                    raise JudgeError("Jev request failed before a response.") from exc
                self._sleep(_backoff(attempt))
                continue
            elapsed_ms = int((self._clock() - started) * 1000)
            status = response.status_code
            last_status = status
            if status == 402:
                raise JudgePaymentRequired(
                    "OpenRouter returned HTTP 402 Payment Required. "
                    "Jev credits are exhausted or the account cannot be billed."
                )
            if status in _RETRYABLE_STATUS and attempt + 1 < attempts:
                self._sleep(_backoff(attempt))
                continue
            if status == 429:
                raise JudgeRateLimited(
                    "OpenRouter rate limited the Jev decisions request (HTTP 429) after retries."
                )
            if status >= 400:
                raise JudgeError(
                    f"Jev request failed with HTTP {status}: {_snippet(response.text)}"
                )
            return _parse_result(response.json(), latency_ms=max(elapsed_ms, 0))
        raise JudgeError(f"Jev request failed with HTTP {last_status}.")


def _backoff(attempt: int) -> float:
    return float(min(8.0, 0.5 * (2**attempt)))


def _snippet(body: str) -> str:
    text = " ".join(body.split())
    if len(text) > 200:
        return text[:200]
    return text


def _parse_result(payload: object, *, latency_ms: int) -> DecisionResult:
    if not isinstance(payload, dict):
        raise JudgeResponseError("Jev response was not a JSON object.")
    answers_raw = payload.get("answers")
    if not isinstance(answers_raw, dict) or not answers_raw:
        raise JudgeResponseError("Jev response did not include answers.")
    answers: dict[str, Answer] = {}
    for key, value in answers_raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise JudgeResponseError("Jev returned an answer that is not an object.")
        answers[key] = _parse_answer(key, value)
    usage = payload.get("usage")
    input_tokens = 0
    output_tokens = 0
    cost: float | None = None
    if isinstance(usage, dict):
        input_tokens = _nonneg_int(usage.get("input_tokens"))
        output_tokens = _nonneg_int(usage.get("output_tokens"))
        cost = _optional_float(usage.get("cost"))
    model = payload.get("model")
    provider = payload.get("provider")
    request_id = payload.get("id")
    return DecisionResult(
        model=model if isinstance(model, str) and model else "",
        answers=answers,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        latency_ms=latency_ms,
        request_id=request_id if isinstance(request_id, str) else None,
        provider=provider if isinstance(provider, str) else None,
    )


def _parse_answer(key: str, value: Mapping[str, object]) -> Answer:
    kind = value.get("type")
    if kind == "noul":
        noul = _required_unit(value.get("noul"), key)
        return NoulAnswer(noul=noul)
    if kind == "choice":
        choice = value.get("choice")
        if not isinstance(choice, str) or not choice:
            raise JudgeResponseError(f"Jev choice answer {key} did not name an option.")
        return ChoiceAnswer(
            choice=choice,
            probabilities=_probability_map(value.get("probabilities")),
            confidence=_optional_unit(value.get("confidence")),
        )
    if kind == "score":
        score = value.get("score")
        if isinstance(score, bool) or not isinstance(score, int | float):
            raise JudgeResponseError(f"Jev score answer {key} did not include a score.")
        score_value = float(score)
        if math.isnan(score_value):
            raise JudgeResponseError(f"Jev score answer {key} was NaN.")
        return ScoreAnswer(
            score=score_value,
            probabilities=_probability_map(value.get("probabilities")),
            confidence=_optional_unit(value.get("confidence")),
            legend=_legend(value.get("legend")),
        )
    raise JudgeResponseError(f"Jev answer {key} has unexpected type {kind!r}.")


def _probability_map(raw: object) -> dict[str, float] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise JudgeResponseError("Jev probabilities were not an object.")
    parsed: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise JudgeResponseError("Jev probability keys must be strings.")
        parsed[key] = _required_unit(value, key)
    return parsed


def _legend(raw: object) -> dict[str, str] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise JudgeResponseError("Jev score legend was not an object.")
    parsed: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str):
            parsed[key] = value
    return parsed


def _required_unit(raw: object, key: str) -> float:
    value = _optional_unit(raw)
    if value is None:
        raise JudgeResponseError(f"Jev answer {key} is missing a probability.")
    return value


def _optional_unit(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise JudgeResponseError("Jev returned a probability that is not a number.")
    value = float(raw)
    if math.isnan(value):
        raise JudgeResponseError("Jev returned a NaN probability.")
    if math.isinf(value) or value < 0.0 or value > 1.0:
        raise JudgeResponseError("Jev returned a probability outside 0-1.")
    return value


def _optional_float(raw: object) -> float | None:
    if raw is None or isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    return float(raw)


def _nonneg_int(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return 0
    return max(0, int(raw))


_GUARD_NO_RETRY = frozenset({400, 401, 402, 403, 404, 422})


class _GuardHttpError(JudgeError):
    """HTTP failure from the chat-guard Decisions call. Carries the status for retry."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def decisions_endpoint(settings: Settings) -> str:
    """Decisions URL for the chat guard.

    ``OPENROUTER_BASE_URL`` ends in ``/api/v1``. The Decisions route does not.
    ``jev_decisions_url`` replaces that derivation when it is set. The lesson
    judge keeps ``OPENROUTER_DECISIONS_URL``.
    """
    override = settings.jev_decisions_url.strip()
    if override:
        return override
    base = settings.openrouter_base_url.rstrip("/")
    suffix = "/api/v1"
    origin = base[: -len(suffix)] if base.endswith(suffix) else base
    return f"{origin}/api/alpha/decisions"


def _redacted_snippet(body: str) -> str:
    text = " ".join(body.split())
    if "Bearer " in text or "sk-or-" in text:
        return "redacted"
    return _snippet(text)


async def _post_guard_once(
    client: httpx.AsyncClient,
    *,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
) -> DecisionResult:
    started = time.perf_counter()
    try:
        response = await client.post(url, json=body, headers=headers)
    except httpx.TimeoutException as exc:
        raise JudgeError("Jev request timed out") from exc
    except httpx.HTTPError as exc:
        raise JudgeError("Jev request failed") from exc
    if response.status_code == 402:
        raise JudgePaymentRequired(
            "OpenRouter returned HTTP 402 Payment Required. "
            "Jev credits are exhausted or the account cannot be billed."
        )
    if response.status_code >= 400:
        raise _GuardHttpError(
            f"OpenRouter decisions HTTP {response.status_code}: {_redacted_snippet(response.text)}",
            status_code=response.status_code,
        )
    try:
        payload: object = response.json()
    except ValueError as exc:
        raise JudgeError("Decisions response was not JSON") from exc
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return _parse_result(payload, latency_ms=max(elapsed_ms, 0))


async def post_decisions(
    *,
    state: str | dict[str, Any] | list[Any],
    questions: dict[str, Any],
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    model: str | None = None,
) -> DecisionResult:
    """One chat-guard Decisions call. Uses the guard timeout and retries once.

    HTTP 402 is not retried. A missing key raises ``JudgeError`` before any
    socket is opened. The posted model is ``model``, else
    ``resolved_guard_jev_model``.
    """
    cfg = settings or get_settings()
    if not cfg.openrouter_configured or not cfg.openrouter_api_key:
        raise JudgeError("OPENROUTER_API_KEY is not configured")
    api_key = cfg.openrouter_api_key.strip()
    url = decisions_endpoint(cfg)
    body: dict[str, Any] = {
        "model": model or cfg.resolved_guard_jev_model,
        "state": state,
        "questions": questions,
    }
    headers = dict(_OPENROUTER_HEADERS)
    headers["Authorization"] = f"Bearer {api_key}"
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(cfg.jev_guard_timeout_seconds))
    try:
        last: JudgeError | None = None
        for attempt in range(2):
            try:
                return await _post_guard_once(http, url=url, body=body, headers=headers)
            except JudgePaymentRequired:
                raise
            except JudgeError as exc:
                last = exc
                status = exc.status_code if isinstance(exc, _GuardHttpError) else None
                if status in _GUARD_NO_RETRY or attempt == 1:
                    raise
        assert last is not None
        raise last
    finally:
        if owns_client:
            await http.aclose()
