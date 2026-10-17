"""OpenRouter chat completions client (OpenAI-compatible API)."""

from __future__ import annotations

import json
from typing import Any, cast

from openai import AsyncOpenAI, OpenAI, OpenAIError

from education_platform.core.config import Settings, get_settings

_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/aushakanuj/education-platform",
    "X-Title": "CurioEd Policy Assistant",
}


class OpenRouterError(RuntimeError):
    """Raised when OpenRouter is misconfigured or the call fails."""


def build_openrouter_client(settings: Settings | None = None) -> AsyncOpenAI:
    cfg = settings or get_settings()
    if not cfg.openrouter_configured:
        raise OpenRouterError(
            "OPENROUTER_API_KEY is not configured. Set it in backend/.env to enable chat."
        )
    return AsyncOpenAI(
        api_key=cfg.openrouter_api_key,
        base_url=cfg.openrouter_base_url,
        default_headers=_OPENROUTER_HEADERS,
    )


def build_openrouter_sync_client(settings: Settings | None = None) -> OpenAI:
    cfg = settings or get_settings()
    if not cfg.openrouter_configured:
        raise OpenRouterError(
            "OPENROUTER_API_KEY is not configured. Set it in backend/.env to enable chat."
        )
    return OpenAI(
        api_key=cfg.openrouter_api_key,
        base_url=cfg.openrouter_base_url,
        default_headers=_OPENROUTER_HEADERS,
    )


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    settings: Settings | None = None,
    temperature: float = 0.0,
    response_json: bool = False,
) -> str:
    cfg = settings or get_settings()
    client = build_openrouter_client(cfg)
    kwargs: dict[str, Any] = {
        "model": cfg.openrouter_model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_json:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        response = await client.chat.completions.create(**kwargs)
    except OpenAIError as exc:
        raise OpenRouterError(str(exc)) from exc
    content = response.choices[0].message.content
    if not content:
        raise OpenRouterError("OpenRouter returned an empty completion")
    return str(content)


async def chat_completion_json(
    messages: list[dict[str, str]],
    *,
    settings: Settings | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    raw = await chat_completion(
        messages,
        settings=settings,
        temperature=temperature,
        response_json=True,
    )
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise OpenRouterError("Expected a JSON object from OpenRouter")
    return data


def chat_completion_sync(
    messages: list[dict[str, str]],
    *,
    settings: Settings | None = None,
    temperature: float = 0.0,
    response_json: bool = False,
) -> str:
    cfg = settings or get_settings()
    client = build_openrouter_sync_client(cfg)
    kwargs: dict[str, Any] = {
        "model": cfg.openrouter_model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_json:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        response = client.chat.completions.create(**kwargs)
    except OpenAIError as exc:
        raise OpenRouterError(str(exc)) from exc
    content = response.choices[0].message.content
    if not content:
        raise OpenRouterError("OpenRouter returned an empty completion")
    return str(content)


def chat_completion_vision_sync(
    messages: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
    temperature: float = 0.0,
) -> str:
    """Sync chat completion whose message content may include image parts.

    ``chat_completion_sync`` is typed for text-only ``dict[str, str]`` messages.
    Formula ingest needs a PNG data-URL part, so this helper accepts nested
    OpenAI vision payloads. The ingest worker is sync, matching this client.
    """
    cfg = settings or get_settings()
    client = build_openrouter_sync_client(cfg)
    try:
        response = client.chat.completions.create(
            model=cfg.openrouter_model,
            messages=cast(Any, messages),
            temperature=temperature,
        )
    except OpenAIError as exc:
        raise OpenRouterError(str(exc)) from exc
    content = response.choices[0].message.content
    if not content:
        raise OpenRouterError("OpenRouter returned an empty completion")
    return str(content)


def chat_completion_json_sync(
    messages: list[dict[str, str]],
    *,
    settings: Settings | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    raw = chat_completion_sync(
        messages,
        settings=settings,
        temperature=temperature,
        response_json=True,
    )
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise OpenRouterError("Expected a JSON object from OpenRouter")
    return data
