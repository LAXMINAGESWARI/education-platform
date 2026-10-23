"""Text-to-SQL calls into the shared LLM gateway.

Legacy call sites keep ``chat_completion`` when the gateway is off, so existing
tests that patch that function still see the same client. Lean always comes
through here.
"""

from __future__ import annotations

from typing import Any

from education_platform.ai_platform.gateway.api import CallContext, complete
from education_platform.core.config import Settings
from education_platform.core.llm import OpenRouterError
from education_platform.modules.text_to_sql.meter import note_tokens


async def gateway_text(step: str, messages: list[dict[str, str]], settings: Settings) -> str:
    result = await complete(
        step,
        messages,
        ctx=CallContext(feature="text_to_sql", settings=settings),
    )
    note_tokens(result.usage.prompt_tokens, result.usage.completion_tokens)
    return result.text


async def gateway_json(
    step: str, messages: list[dict[str, str]], settings: Settings
) -> dict[str, Any]:
    result = await complete(
        step,
        messages,
        ctx=CallContext(feature="text_to_sql", settings=settings),
        response_format={"type": "json_object"},
    )
    note_tokens(result.usage.prompt_tokens, result.usage.completion_tokens)
    if result.parsed_json is None:
        raise OpenRouterError("Expected a JSON object from OpenRouter")
    return result.parsed_json
