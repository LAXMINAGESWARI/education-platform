"""Unit tests for the text-to-SQL question_validator node.

No database or network needed — classify_noul is monkeypatched throughout except
for the one test that deliberately exercises the real "OpenRouter not configured" path.
"""

from __future__ import annotations

import sys
from typing import Protocol, cast

import pytest

from education_platform.core.config import Settings
from education_platform.modules.judge.errors import JudgeError, JudgePaymentRequired
from education_platform.modules.text_to_sql.nodes.question_validator import question_validator
from education_platform.modules.text_to_sql.state import (
    OFF_TOPIC_REJECTED,
    TextToSQLState,
    error_category,
)


class _QuestionValidatorModule(Protocol):
    async def classify_noul(
        self, *, state: object, question: object, settings: Settings
    ) -> float: ...

    def get_settings(self) -> Settings: ...


# Same sys.modules reach-around injection_guard.py's own test file uses -- nodes/__init__.py
# rebinds the `nodes.question_validator` *attribute* to the function itself, so
# monkeypatching module-level names has to go through the real submodule.
_MODULE = cast(
    _QuestionValidatorModule,
    sys.modules["education_platform.modules.text_to_sql.nodes.question_validator"],
)


@pytest.fixture(autouse=True)
def _openrouter_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every test here expects to reach the classifier call (its classify_noul mock)
    # rather than take the "not configured, fail open" branch -- scoped to this module's
    # `get_settings` reference only, not the process-wide `get_settings` lru_cache, so it
    # can't leak into other test files that rely on the opposite (unconfigured) default.
    # The "not configured" test below overrides this within its own test.
    monkeypatch.setattr(
        _MODULE, "get_settings", lambda: Settings(openrouter_api_key="test-key-not-a-real-key")
    )


def _state(question: str, **overrides: object) -> TextToSQLState:
    state: TextToSQLState = {
        "question": question,
        "user_id": "u1",
        "user_role": "teacher",
        "institution_id": "i1",
        "error": None,
        "retry_count": 0,
        "audit_entry": None,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _fake_classifier(probability: float) -> object:
    calls: list[dict[str, object]] = []

    async def _fake(*, state: object, question: object, settings: Settings) -> float:
        calls.append({"state": state, "question": question, "settings": settings})
        return probability

    return _fake, calls


# --- Off-topic questions get rejected ---------------------------------------------------


async def test_classifier_flags_an_off_topic_question(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _fake_classifier(0.95)
    monkeypatch.setattr(_MODULE, "classify_noul", fake)

    result = await question_validator(_state("What's the weather like tomorrow?"))

    assert error_category(result["error"]) == OFF_TOPIC_REJECTED


# --- Ordinary in-domain questions pass through unchanged, even when blunt/direct --------


@pytest.mark.parametrize(
    "question",
    [
        "How many students do I teach in total?",
        "Show me everyone in my class.",
        "I need to see all sections.",
        "What subject do I teach?",
    ],
)
async def test_in_domain_question_passes_through_unchanged(
    monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    fake, calls = _fake_classifier(0.1)
    monkeypatch.setattr(_MODULE, "classify_noul", fake)

    state = _state(question)
    result = await question_validator(state)

    assert result["error"] is None
    assert result["question"] == state["question"]
    assert result["user_id"] == state["user_id"]
    assert len(calls) == 1


async def test_classifier_receives_only_the_current_question_no_identity_leaked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, calls = _fake_classifier(0.1)
    monkeypatch.setattr(_MODULE, "classify_noul", fake)

    await question_validator(_state("What subjects do I teach?", user_id="secret-uuid-123"))

    sent = calls[0]["state"]
    assert isinstance(sent, dict)
    assert "secret-uuid-123" not in str(sent)


# --- Fail-open when unconfigured, fail-closed when erroring -----------------------------


async def test_openrouter_not_configured_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_MODULE, "get_settings", lambda: Settings(openrouter_api_key=None))

    async def _fail_if_called(*args: object, **kwargs: object) -> float:
        raise AssertionError("classifier must not be called when OpenRouter isn't configured")

    monkeypatch.setattr(_MODULE, "classify_noul", _fail_if_called)

    result = await question_validator(_state("What's the weather like tomorrow?"))

    assert result["error"] is None


async def test_openrouter_error_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raising(*args: object, **kwargs: object) -> float:
        raise JudgeError("network blew up")

    monkeypatch.setattr(_MODULE, "classify_noul", _raising)

    result = await question_validator(_state("How many students do I teach in total?"))

    assert error_category(result["error"]) == OFF_TOPIC_REJECTED


async def test_malformed_classifier_response_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _missing(**kwargs: object) -> float:
        _ = kwargs
        raise JudgeError("Decisions response missing a noul probability")

    monkeypatch.setattr(_MODULE, "classify_noul", _missing)

    result = await question_validator(_state("How many students do I teach in total?"))

    assert error_category(result["error"]) == OFF_TOPIC_REJECTED


async def test_off_topic_keeps_the_injection_guard_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _fake_classifier(0.1)
    monkeypatch.setattr(_MODULE, "classify_noul", fake)
    prior = {"guard_stage": "jev", "injection_probability": 0.2}

    result = await question_validator(
        _state("How many students do I teach in total?", audit_entry=prior)
    )

    assert result["error"] is None
    assert result["audit_entry"]["guard_stage"] == "jev"
    assert result["audit_entry"]["injection_probability"] == 0.2
    assert result["audit_entry"]["off_topic_probability"] == 0.1


async def test_off_topic_error_keeps_the_injection_guard_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raising(*args: object, **kwargs: object) -> float:
        raise JudgeError("network blew up")

    monkeypatch.setattr(_MODULE, "classify_noul", _raising)
    prior = {"guard_stage": "gpt", "injection_probability": None}

    result = await question_validator(
        _state("How many students do I teach in total?", audit_entry=prior)
    )

    assert error_category(result["error"]) == OFF_TOPIC_REJECTED
    assert result["audit_entry"]["guard_stage"] == "gpt"


async def test_off_topic_402_uses_gpt_and_keeps_the_injection_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _pay(*args: object, **kwargs: object) -> float:
        raise JudgePaymentRequired("OpenRouter returned HTTP 402")

    async def _gpt(**kwargs: object) -> bool:
        assert kwargs["field"] == "off_topic"
        return False

    monkeypatch.setattr(_MODULE, "classify_noul", _pay)
    monkeypatch.setattr(_MODULE, "legacy_gpt_flag", _gpt)
    prior = {"guard_stage": "jev"}

    allowed = await question_validator(_state("What subject do I teach?", audit_entry=prior))

    assert allowed["error"] is None
    assert allowed["audit_entry"]["guard_stage"] == "jev"

    async def _block(**kwargs: object) -> bool:
        assert kwargs["field"] == "off_topic"
        return True

    monkeypatch.setattr(_MODULE, "legacy_gpt_flag", _block)
    refused = await question_validator(
        _state("What's the weather like tomorrow?", audit_entry=dict(prior))
    )

    assert error_category(refused["error"]) == OFF_TOPIC_REJECTED
    assert refused["audit_entry"]["guard_stage"] == "jev"
