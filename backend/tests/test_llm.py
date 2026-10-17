"""OpenRouter client: tests never use a live key; SDK errors fail closed."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import OpenAIError

from education_platform.core.config import Settings, get_settings
from education_platform.core.llm import (
    _OPENROUTER_HEADERS,
    OpenRouterError,
    build_openrouter_client,
    chat_completion,
    chat_completion_sync,
    chat_completion_vision_sync,
)


def test_openrouter_title_names_the_product() -> None:
    assert _OPENROUTER_HEADERS["X-Title"] == "CurioEd Policy Assistant"


def test_pytest_defaults_do_not_use_live_openrouter() -> None:
    get_settings.cache_clear()
    assert get_settings().openrouter_configured is False
    with pytest.raises(OpenRouterError, match="not configured"):
        build_openrouter_client()


async def test_chat_completion_wraps_openai_errors() -> None:
    settings = Settings(openrouter_api_key="test-key")
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=OpenAIError("upstream down"))
    with (
        patch("education_platform.core.llm.build_openrouter_client", return_value=client),
        pytest.raises(OpenRouterError, match="upstream down"),
    ):
        await chat_completion([{"role": "user", "content": "hi"}], settings=settings)


def test_chat_completion_sync_wraps_openai_errors() -> None:
    settings = Settings(openrouter_api_key="test-key")
    client = MagicMock()
    client.chat.completions.create.side_effect = OpenAIError("upstream down")
    with (
        patch("education_platform.core.llm.build_openrouter_sync_client", return_value=client),
        pytest.raises(OpenRouterError, match="upstream down"),
    ):
        chat_completion_sync([{"role": "user", "content": "hi"}], settings=settings)


def test_chat_completion_vision_sync_wraps_openai_errors() -> None:
    settings = Settings(openrouter_api_key="test-key")
    client = MagicMock()
    client.chat.completions.create.side_effect = OpenAIError("upstream down")
    with (
        patch("education_platform.core.llm.build_openrouter_sync_client", return_value=client),
        pytest.raises(OpenRouterError, match="upstream down"),
    ):
        chat_completion_vision_sync(
            [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            settings=settings,
        )
