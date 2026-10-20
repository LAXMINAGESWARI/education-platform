"""One async client per event loop, and one sync client, for OpenRouter.

Callers on the same loop share the HTTP connection pool. A client built for one
loop is not reused on another: workers that call ``asyncio.run`` each get their
own. The SDK retry counter stays at zero so the gateway's own backoff is the
only retry loop.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from weakref import WeakKeyDictionary

import httpx
from openai import AsyncOpenAI, OpenAI

from education_platform.core.config import Settings, get_settings
from education_platform.core.llm import OpenRouterError

HTTP_REFERER = "https://github.com/aushakanuj/education-platform"
DEFAULT_TITLE = "CurioEd"


class _NoLoop:
    """Cache key for ``get_async_client`` calls made with no running loop."""


_NO_LOOP = _NoLoop()
_Slot = asyncio.AbstractEventLoop | _NoLoop

_lock = threading.Lock()
_async_clients: WeakKeyDictionary[_Slot, dict[tuple[Any, ...], AsyncOpenAI]] = WeakKeyDictionary()
_async_http_clients: WeakKeyDictionary[_Slot, httpx.AsyncClient] = WeakKeyDictionary()
_async_http_claimed = False
_sync_client: OpenAI | None = None
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
    global _sync_client, _sync_key, _async_http_claimed
    with _lock:
        _async_clients.clear()
        _async_http_clients.clear()
        _async_http_claimed = False
        _sync_client = None
        _sync_key = None


def get_async_client(settings: Settings | None = None) -> AsyncOpenAI:
    cfg = settings or get_settings()
    api_key = _api_key(cfg)
    slot = _current_slot()
    key = (api_key, cfg.openrouter_base_url, id(_async_http))
    with _lock:
        bucket = _async_clients.get(slot)
        if bucket is None:
            bucket = {}
            _async_clients[slot] = bucket
        existing = bucket.get(key)
        if existing is not None:
            return existing
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": cfg.openrouter_base_url,
            "default_headers": _default_headers(),
            "max_retries": 0,
        }
        http_client = _async_http_for_slot(slot)
        if http_client is not None:
            kwargs["http_client"] = http_client
        created = AsyncOpenAI(**kwargs)
        bucket[key] = created
        return created


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


def _current_slot() -> _Slot:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return _NO_LOOP


def _async_http_for_slot(slot: _Slot) -> httpx.AsyncClient | None:
    """Return an HTTP client bound to ``slot``.

    The caller holds ``_lock``. The first slot may use the client installed by
    tests. Every other loop gets its own client so a closed loop is not reused.
    """
    global _async_http_claimed
    if _async_http is None:
        return None
    cached = _async_http_clients.get(slot)
    if cached is not None:
        return cached
    if not _async_http_claimed:
        _async_http_claimed = True
        _async_http_clients[slot] = _async_http
        return _async_http
    sibling = _clone_async_http(_async_http)
    _async_http_clients[slot] = sibling
    return sibling


def _clone_async_http(client: httpx.AsyncClient) -> httpx.AsyncClient:
    transport = client._transport
    if isinstance(transport, httpx.MockTransport):
        return httpx.AsyncClient(transport=httpx.MockTransport(transport.handler))
    return httpx.AsyncClient(transport=transport)


def _api_key(cfg: Settings) -> str:
    if not cfg.openrouter_configured or cfg.openrouter_api_key is None:
        raise OpenRouterError(
            "OPENROUTER_API_KEY is not configured. Set it in backend/.env to enable chat."
        )
    return cfg.openrouter_api_key


def _default_headers() -> dict[str, str]:
    return {"HTTP-Referer": HTTP_REFERER, "X-Title": DEFAULT_TITLE}
