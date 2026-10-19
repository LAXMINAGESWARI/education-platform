"""One process-wide async client and one sync client for OpenRouter.

Callers share the HTTP connection pool. The SDK retry counter stays at zero so
the gateway's own backoff is the only retry loop.
"""

from __future__ import annotations

import threading
from typing import Any

import httpx
from openai import AsyncOpenAI, OpenAI

from education_platform.core.config import Settings, get_settings
from education_platform.core.llm import OpenRouterError

HTTP_REFERER = "https://github.com/aushakanuj/education-platform"
DEFAULT_TITLE = "CurioEd"

_lock = threading.Lock()
_async_client: AsyncOpenAI | None = None
_sync_client: OpenAI | None = None
_async_key: tuple[Any, ...] | None = None
_sync_key: tuple[Any, ...] | None = None
_async_http: httpx.AsyncClient | None = None
_sync_http: httpx.Client | None = None


def bind_http_clients(
    *,
    async_client: httpx.AsyncClient | None = None,
    sync_client: httpx.Client | None = None,
) -> None:
    """Install fake transports for tests, then drop any cached SDK clients."""
    global _async_http, _sync_http
    _async_http = async_client
    _sync_http = sync_client
    reset_clients()


def reset_clients() -> None:
    """Drop cached SDK clients so the next call rebuilds them."""
    global _async_client, _sync_client, _async_key, _sync_key
    with _lock:
        _async_client = None
        _sync_client = None
        _async_key = None
        _sync_key = None


def get_async_client(settings: Settings | None = None) -> AsyncOpenAI:
    cfg = settings or get_settings()
    api_key = _api_key(cfg)
    key = (api_key, cfg.openrouter_base_url, id(_async_http))
    global _async_client, _async_key
    with _lock:
        if _async_client is not None and _async_key == key:
            return _async_client
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": cfg.openrouter_base_url,
            "default_headers": _default_headers(),
            "max_retries": 0,
        }
        if _async_http is not None:
            kwargs["http_client"] = _async_http
        _async_client = AsyncOpenAI(**kwargs)
        _async_key = key
        return _async_client


def get_sync_client(settings: Settings | None = None) -> OpenAI:
    cfg = settings or get_settings()
    api_key = _api_key(cfg)
    key = (api_key, cfg.openrouter_base_url, id(_sync_http))
    global _sync_client, _sync_key
    with _lock:
        if _sync_client is not None and _sync_key == key:
            return _sync_client
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": cfg.openrouter_base_url,
            "default_headers": _default_headers(),
            "max_retries": 0,
        }
        if _sync_http is not None:
            kwargs["http_client"] = _sync_http
        _sync_client = OpenAI(**kwargs)
        _sync_key = key
        return _sync_client


def _api_key(cfg: Settings) -> str:
    if not cfg.openrouter_configured or cfg.openrouter_api_key is None:
        raise OpenRouterError(
            "OPENROUTER_API_KEY is not configured. Set it in backend/.env to enable chat."
        )
    return cfg.openrouter_api_key


def _default_headers() -> dict[str, str]:
    return {"HTTP-Referer": HTTP_REFERER, "X-Title": DEFAULT_TITLE}
