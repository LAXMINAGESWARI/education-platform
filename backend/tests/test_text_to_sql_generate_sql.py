"""Unit tests for the text-to-SQL generate_sql node.

No database or network needed — chat_completion is monkeypatched throughout except for
one test that deliberately exercises the real "OpenRouter not configured" path.
"""

from __future__ import annotations

import sys
from typing import Protocol, cast

import pytest

from education_platform.core.config import Settings
from education_platform.modules.assistant.openrouter import OpenRouterError
from education_platform.modules.text_to_sql.nodes.generate_sql import generate_sql
from education_platform.modules.text_to_sql.state import (
    LLM_ERROR,
    VALIDATION_ERROR,
    TextToSQLState,
    error_category,
    format_error,
)


class _GenerateSqlModule(Protocol):
    """Typed view of generate_sql.py's module namespace, for patching chat_completion
    and get_settings where generate_sql.py actually looks them up.
    """

    async def chat_completion(
        self, messages: list[dict[str, str]], *, settings: Settings, temperature: float = 0.0
    ) -> str: ...

    def get_settings(self) -> Settings: ...


# `nodes/__init__.py` does `from .generate_sql import generate_sql as generate_sql`,
# which rebinds the `nodes.generate_sql` *attribute* from the submodule to the
# function (same gotcha as load_schema — see test_text_to_sql_load_schema.py). Reach
# the real submodule via sys.modules to monkeypatch its module-level names.
_MODULE = cast(
    _GenerateSqlModule, sys.modules["education_platform.modules.text_to_sql.nodes.generate_sql"]
)


def _base_state(**overrides: object) -> TextToSQLState:
    state: TextToSQLState = {
        "question": "How many students passed the last quiz?",
        "user_id": "u1",
        "user_role": "admin",
        "schema_context": "#### `quiz_attempts`\n...",
        "generated_sql": None,
        "error": None,
        "retry_count": 0,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _fake_chat_completion(
    response: str,
) -> object:
    captured: dict[str, object] = {}

    async def _fake(
        messages: list[dict[str, str]], *, settings: Settings, temperature: float = 0.0
    ) -> str:
        captured["messages"] = messages
        return response

    return _fake, captured


# --- First call: no prior error --------------------------------------------------


async def test_first_call_generates_sql_without_incrementing_retry_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, _ = _fake_chat_completion("```sql\nSELECT COUNT(*) FROM quiz_attempts\n```")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    result = await generate_sql(_base_state(retry_count=0, error=None))

    assert result["retry_count"] == 0
    assert result["generated_sql"] == "SELECT COUNT(*) FROM quiz_attempts"
    assert result["error"] is None


async def test_first_call_preserves_nonzero_starting_retry_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # retry_count only moves when *this* node sees a pre-existing error; it must not
    # reset or otherwise touch a starting value it didn't itself increment.
    fake, _ = _fake_chat_completion("SELECT 1")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    result = await generate_sql(_base_state(retry_count=2, error=None))

    assert result["retry_count"] == 2


async def test_success_does_not_write_validated_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _fake_chat_completion("SELECT 1")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    result = await generate_sql(_base_state())

    assert "validated_sql" not in result


# --- Retry: prior error present ----------------------------------------------------


async def test_retry_call_increments_retry_count_before_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, _ = _fake_chat_completion("SELECT 2")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    result = await generate_sql(
        _base_state(retry_count=1, error=format_error(VALIDATION_ERROR, "unknown column foo"))
    )

    assert result["retry_count"] == 2


async def test_retry_count_increments_correctly_approaching_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This node only increments; whether 3 triggers honest_refusal is graph.py's
    # existing, separately-tested routing (Task 2) — not re-tested here.
    fake, _ = _fake_chat_completion("SELECT 3")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    result = await generate_sql(
        _base_state(retry_count=2, error=format_error(VALIDATION_ERROR, "still wrong"))
    )

    assert result["retry_count"] == 3


async def test_retry_prompt_includes_prior_sql_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, captured = _fake_chat_completion("SELECT fixed FROM quiz_attempts")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    await generate_sql(
        _base_state(
            retry_count=1,
            error=format_error(VALIDATION_ERROR, "column 'foo' does not exist"),
            generated_sql="SELECT foo FROM quiz_attempts",
        )
    )

    messages = cast(list[dict[str, str]], captured["messages"])
    user_content = messages[-1]["content"]
    assert "SELECT foo FROM quiz_attempts" in user_content
    assert "column 'foo' does not exist" in user_content


async def test_first_call_prompt_has_no_retry_scaffolding(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, captured = _fake_chat_completion("SELECT 1")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    await generate_sql(_base_state(retry_count=0, error=None))

    user_content = cast(list[dict[str, str]], captured["messages"])[-1]["content"]
    assert "previous attempt" not in user_content.lower()


async def test_prompt_includes_question_schema_and_role(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, captured = _fake_chat_completion("SELECT 1")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    await generate_sql(
        _base_state(
            question="How many students are enrolled?",
            schema_context="#### `student_subject_enrollments`\n...",
            user_role="teacher",
        )
    )

    messages = cast(list[dict[str, str]], captured["messages"])
    assert messages[0]["content"].startswith("You are a SQL generation assistant for CurioEd's")
    user_content = messages[-1]["content"]
    assert "How many students are enrolled?" in user_content
    assert "student_subject_enrollments" in user_content
    assert "teacher" in user_content


async def test_prompt_documents_both_common_mastery_quiz_scope_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, captured = _fake_chat_completion("SELECT 1")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    await generate_sql(_base_state())

    system_content = cast(list[dict[str, str]], captured["messages"])[0]["content"]
    assert "quiz_scope = 'subtopic_mastery'" in system_content
    assert "quiz_scope = 'topic_mastery'" in system_content
    assert "JOIN subtopics st ON st.id = cmq.subtopic_id" in system_content
    assert "JOIN topics t ON t.id = cmq.topic_id" in system_content
    assert "exactly one scope" in system_content


async def test_prompt_documents_verified_join_and_timestamp_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, captured = _fake_chat_completion("SELECT 1")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    await generate_sql(_base_state())

    system_content = cast(list[dict[str, str]], captured["messages"])[0]["content"]
    assert "quiz_attempts.student_subject_enrollment_id" in system_content
    assert "student_subject_enrollments.id" in system_content
    assert "submitted_at" in system_content
    assert "scored_at" in system_content
    assert "returns zero rows because of an unrelated-ID join is incorrect" in system_content


# --- SQL extraction ------------------------------------------------------------


async def test_extracts_sql_from_fenced_code_block(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _fake_chat_completion(
        "Here you go:\n```sql\nSELECT id FROM users\n```\nLet me know if you need changes."
    )
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    result = await generate_sql(_base_state())

    assert result["generated_sql"] == "SELECT id FROM users"


async def test_strips_trailing_semicolon(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _fake_chat_completion("SELECT id FROM users;")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    result = await generate_sql(_base_state())

    assert result["generated_sql"] == "SELECT id FROM users"


# --- LLM failure: distinguishable from a validation retry --------------------------


async def test_llm_api_error_is_distinguishable_from_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raising(
        messages: list[dict[str, str]], *, settings: Settings, temperature: float = 0.0
    ) -> str:
        raise OpenRouterError("connection reset")

    monkeypatch.setattr(_MODULE, "chat_completion", _raising)

    result = await generate_sql(_base_state())

    assert result["error"] is not None
    assert error_category(result["error"]) == LLM_ERROR
    assert "connection reset" in result["error"]
    # Distinguishable from how a validate_sql rejection is categorized, not just by
    # eyeballing the wording — the two categories are literally different values.
    validation_example = format_error(VALIDATION_ERROR, "column 'foo' does not exist")
    assert error_category(validation_example) == VALIDATION_ERROR
    assert error_category(result["error"]) != error_category(validation_example)


async def test_llm_api_error_does_not_set_generated_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raising(
        messages: list[dict[str, str]], *, settings: Settings, temperature: float = 0.0
    ) -> str:
        raise OpenRouterError("timeout")

    monkeypatch.setattr(_MODULE, "chat_completion", _raising)

    result = await generate_sql(_base_state(generated_sql=None))

    assert result.get("generated_sql") is None


async def test_llm_api_error_still_increments_retry_count_if_this_was_a_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The increment (owned entirely by this node, per spec) happens unconditionally on
    # a pre-existing error, regardless of what happens next in *this* call.
    async def _raising(
        messages: list[dict[str, str]], *, settings: Settings, temperature: float = 0.0
    ) -> str:
        raise OpenRouterError("boom")

    monkeypatch.setattr(_MODULE, "chat_completion", _raising)

    result = await generate_sql(
        _base_state(retry_count=1, error=format_error(VALIDATION_ERROR, "bad SQL"))
    )

    assert result["retry_count"] == 2


async def test_empty_llm_response_is_treated_as_llm_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, _ = _fake_chat_completion("```sql\n\n```")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    result = await generate_sql(_base_state())

    assert result["error"] is not None
    assert error_category(result["error"]) == LLM_ERROR
    assert result.get("generated_sql") is None


async def test_unconfigured_openrouter_surfaces_as_llm_failure_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No chat_completion mock here — exercises the real build_openrouter_client()
    # "not configured" raise path, not just a simulated OpenRouterError.
    monkeypatch.setattr(_MODULE, "get_settings", lambda: Settings(openrouter_api_key=None))

    result = await generate_sql(_base_state())

    assert result["error"] is not None
    assert error_category(result["error"]) == LLM_ERROR
    assert result.get("generated_sql") is None
