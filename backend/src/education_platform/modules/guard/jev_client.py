"""OpenRouter Decisions API client for TypeSafe Jev.

Jev is a decisions model (output modality ``decisions``), not a chat model.
OpenRouter documents the call as ``POST https://openrouter.ai/api/alpha/decisions``
with ``model``, ``state``, and typed ``questions`` — not ``/api/v1/chat/completions``.
See https://openrouter.ai/docs/guides/community/jev and the Decisions API reference
https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request.

This module is intentionally small so a later lesson-judge client can share it
or be deduped without touching ``core/llm.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from education_platform.core.config import Settings, get_settings

_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/aushakanuj/education-platform",
    "X-Title": "CurioEd",
}
_NO_RETRY_STATUS = frozenset({400, 401, 402, 403, 404, 422})


class JevError(RuntimeError):
    """Jev could not return a usable decision. Callers fail open."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class DecisionsResponse:
    model: str
    answers: dict[str, Any]
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None


def decisions_endpoint(settings: Settings) -> str:
    """Decisions API URL. ``OPENROUTER_BASE_URL`` ends in ``/api/v1``; this does not."""
    override = settings.jev_decisions_url.strip()
    if override:
        return override
    base = settings.openrouter_base_url.rstrip("/")
    suffix = "/api/v1"
    origin = base[: -len(suffix)] if base.endswith(suffix) else base
    return f"{origin}/api/alpha/decisions"


def _safe_detail(text: str) -> str:
    cleaned = " ".join(text.split())
    if "Bearer " in cleaned or "sk-or-" in cleaned:
        return "redacted"
    return cleaned[:240]


def _parse_body(payload: object) -> DecisionsResponse:
    if not isinstance(payload, dict):
        raise JevError("Decisions response was not a JSON object")
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise JevError("Decisions response did not include answers")
    usage = payload.get("usage")
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None
    if isinstance(usage, dict):
        raw_in = usage.get("input_tokens")
        raw_out = usage.get("output_tokens")
        raw_cost = usage.get("cost")
        if isinstance(raw_in, int) and not isinstance(raw_in, bool):
            input_tokens = raw_in
        if isinstance(raw_out, int) and not isinstance(raw_out, bool):
            output_tokens = raw_out
        if isinstance(raw_cost, (int, float)) and not isinstance(raw_cost, bool):
            cost = float(raw_cost)
    model = payload.get("model")
    return DecisionsResponse(
        model=model if isinstance(model, str) else "",
        answers=answers,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
    )


async def _post_once(
    client: httpx.AsyncClient,
    *,
    url: str,
    api_key: str,
    body: dict[str, Any],
) -> DecisionsResponse:
    try:
        response = await client.post(
            url,
            headers={
                **_OPENROUTER_HEADERS,
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    except httpx.TimeoutException as exc:
        raise JevError("Jev request timed out") from exc
    except httpx.HTTPError as exc:
        raise JevError("Jev request failed") from exc
    if response.status_code == 402:
        raise JevError("OpenRouter returned 402 (insufficient credits)", status_code=402)
    if response.status_code >= 400:
        raise JevError(
            f"OpenRouter decisions HTTP {response.status_code}: {_safe_detail(response.text)}",
            status_code=response.status_code,
        )
    try:
        payload: object = response.json()
    except ValueError as exc:
        raise JevError("Decisions response was not JSON") from exc
    return _parse_body(payload)


async def post_decisions(
    *,
    state: dict[str, Any] | list[Any] | str,
    questions: dict[str, Any],
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> DecisionsResponse:
    """One Decisions API call. Times out around ``jev_guard_timeout_seconds`` and retries once.

    HTTP 402 (credits exhausted) is not retried. Callers choose fail-open or fail-closed.
    """
    cfg = settings or get_settings()
    if not cfg.openrouter_configured or not cfg.openrouter_api_key:
        raise JevError("OPENROUTER_API_KEY is not configured")
    url = decisions_endpoint(cfg)
    body: dict[str, Any] = {
        "model": cfg.jev_guard_model,
        "state": state,
        "questions": questions,
    }
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(cfg.jev_guard_timeout_seconds))
    try:
        last: JevError | None = None
        for attempt in range(2):
            try:
                return await _post_once(http, url=url, api_key=cfg.openrouter_api_key, body=body)
            except JevError as exc:
                last = exc
                if exc.status_code in _NO_RETRY_STATUS or attempt == 1:
                    raise
        assert last is not None
        raise last
    finally:
        if owns_client:
            await http.aclose()
