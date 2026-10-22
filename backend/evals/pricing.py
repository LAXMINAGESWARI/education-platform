"""Token counts and public OpenRouter prices.

The model catalog is ``GET https://openrouter.ai/api/v1/models``. That request
needs no key and does not call a model. A failed fetch falls back to the rates
already written down in this repo.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

import httpx
import tiktoken

from education_platform.modules.judge.client import INPUT_USD_PER_MILLION

MODELS_URL = "https://openrouter.ai/api/v1/models"
_PER_MILLION = 1_000_000

# USD per million tokens, matching evals.guard.collect.GPT_PRICES.
_FALLBACK_PER_MILLION: dict[str, tuple[float, float]] = {
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
}
_UNKNOWN_PER_MILLION = (5.00, 15.00)
_JEV_COMPLETION_PER_MILLION = 0.0

_encoder: Any = None
_tokenizer_name: str | None = None


@dataclass(frozen=True, slots=True)
class ModelPrice:
    prompt_per_token: float
    completion_per_token: float
    source: str


def tokenizer_name() -> str:
    _encoder_for()
    return _tokenizer_name or "char/4"


def count_text(text: str) -> int:
    encoder = _encoder_for()
    if encoder is None:
        return math.ceil(len(text) / 4)
    return len(encoder.encode(text))


def count_messages(messages: list[dict[str, str]]) -> int:
    """OpenAI chat overhead: 4 tokens per message, plus 3 for the reply primer."""
    total = 3
    for message in messages:
        total += 4
        total += count_text(message.get("role") or "")
        total += count_text(message.get("content") or "")
    return total


def count_json(payload: object) -> int:
    return count_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def parse_catalog_prices(
    catalog: list[dict[str, Any]], model_ids: list[str]
) -> dict[str, ModelPrice]:
    by_id = {item.get("id"): item for item in catalog if isinstance(item, dict)}
    priced: dict[str, ModelPrice] = {}
    for model_id in model_ids:
        entry = by_id.get(model_id)
        parsed = _entry_price(entry) if isinstance(entry, dict) else None
        if parsed is not None:
            prompt, completion = parsed
            priced[model_id] = ModelPrice(prompt, completion, "openrouter")
        else:
            priced[model_id] = _fallback_price(model_id)
    return priced


def load_prices(
    model_ids: list[str], *, timeout: float = 20.0
) -> tuple[dict[str, ModelPrice], str]:
    unique = list(dict.fromkeys(model_ids))
    try:
        response = httpx.get(MODELS_URL, timeout=timeout)
        response.raise_for_status()
        body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise ValueError("OpenRouter model list has no data array")
        prices = parse_catalog_prices(data, unique)
    except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
        prices = {model_id: _fallback_price(model_id) for model_id in unique}
        return prices, f"price fetch failed ({type(exc).__name__}); using in-repo fallback rates"
    missing = [model_id for model_id, price in prices.items() if price.source != "openrouter"]
    if missing:
        listed = ", ".join(missing)
        return prices, f"public catalog; fallback rates for {listed}"
    return prices, "public catalog https://openrouter.ai/api/v1/models"


def _entry_price(entry: dict[str, Any]) -> tuple[float, float] | None:
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        return None
    prompt = _as_per_token(pricing.get("prompt"))
    completion = _as_per_token(pricing.get("completion"))
    if prompt is None or completion is None:
        return None
    return prompt, completion


def _as_per_token(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, str)):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    if number < 0:
        return None
    # The public field is USD per token. A value at or above one cent per token
    # is the per-million figure some copies of the table still use.
    if number >= 0.01:
        return number / _PER_MILLION
    return number


def _fallback_price(model_id: str) -> ModelPrice:
    if model_id.startswith("typesafe/jev") or "jev" in model_id:
        return ModelPrice(
            INPUT_USD_PER_MILLION / _PER_MILLION,
            _JEV_COMPLETION_PER_MILLION,
            "fallback jev in-repo $0.042/M input, completion free",
        )
    prompt, completion = _FALLBACK_PER_MILLION.get(model_id, _UNKNOWN_PER_MILLION)
    source = "fallback in-repo" if model_id in _FALLBACK_PER_MILLION else "fallback pessimistic"
    return ModelPrice(prompt / _PER_MILLION, completion / _PER_MILLION, source)


def _encoder_for() -> Any:
    global _encoder, _tokenizer_name
    if _tokenizer_name is not None:
        return _encoder
    try:
        _encoder = tiktoken.get_encoding("cl100k_base")
        _tokenizer_name = "tiktoken cl100k_base"
    except (OSError, ValueError, KeyError):
        _encoder = None
        _tokenizer_name = "char/4"
    return _encoder
