"""Jev HTTP adapter against the documented decisions response. No live calls."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from education_platform.core.config import Settings
from education_platform.modules.judge.client import HttpJudge, decisions_endpoint
from education_platform.modules.judge.errors import (
    JudgePaymentRequired,
    JudgeRateLimited,
    JudgeResponseError,
)
from education_platform.modules.judge.types import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionRequest,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "jev_decision_response.json").read_text(encoding="utf-8")
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openrouter_api_key": "test-key",
        "jev_model": "typesafe/jev-1.14",
        "jev_base_url": "",
        "jev_timeout_seconds": 30.0,
        "jev_max_retries": 2,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _request() -> DecisionRequest:
    return DecisionRequest(
        state={"ticket": "My checkout page shows a blank screen after I click Pay."},
        questions={
            "is_bug": NoulQuestion(
                instructions="Is the customer reporting a software defect?",
                true_meaning="Broken behavior.",
                false_meaning="A question or feature request.",
            ),
            "team": ChoiceQuestion(
                instructions="Which team should own this ticket?",
                criteria={"payments": "Checkout.", "frontend": "Rendering."},
            ),
            "urgency": ScoreQuestion(
                instructions="How urgent is this ticket?",
                criteria=("Can wait", "This week", "Blocking revenue"),
            ),
        },
        session_id="run-1",
    )


def test_openrouter_endpoint_is_the_documented_decisions_route() -> None:
    assert decisions_endpoint(_settings()) == "https://openrouter.ai/api/alpha/decisions"
    assert (
        decisions_endpoint(_settings(jev_base_url="http://127.0.0.1:8080"))
        == "http://127.0.0.1:8080/v1/systemone"
    )


def test_posts_documented_body_and_parses_probabilities() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json=_FIXTURE)

    judge = HttpJudge(
        _settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )
    result = judge.decide(_request())

    assert seen["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert seen["authorization"] == "Bearer test-key"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "typesafe/jev-1.14"
    assert body["session_id"] == "run-1"
    assert body["questions"]["is_bug"]["type"] == "noul"
    assert body["questions"]["team"]["criteria"]["payments"] == "Checkout."
    assert body["questions"]["urgency"]["criteria"] == ["Can wait", "This week", "Blocking revenue"]
    assert result.model == "typesafe/jev-1.14-20261001"
    assert result.provider == "TypeSafe"
    assert result.request_id == "gen-dec-1789738314-X5e5eKGQdvR9rblyX250"
    assert result.input_tokens == 476
    assert result.cost_usd == pytest.approx(0.000019992)
    bug = result.answers["is_bug"]
    team = result.answers["team"]
    urgency = result.answers["urgency"]
    assert isinstance(bug, NoulAnswer)
    assert bug.noul == pytest.approx(0.96)
    assert isinstance(team, ChoiceAnswer)
    assert team.choice == "payments"
    assert team.probabilities is not None
    assert team.probabilities["payments"] == pytest.approx(0.84)
    assert isinstance(urgency, ScoreAnswer)
    assert urgency.score == pytest.approx(1.99)
    assert urgency.legend is not None
    assert urgency.legend["2"] == "Blocking revenue right now"


def test_local_openjev_uses_systemone_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://127.0.0.1:8080/v1/systemone"
        return httpx.Response(
            200,
            json={
                "model": "openjev-latest",
                "answers": {"is_bug": {"type": "noul", "noul": 0.5}},
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        )

    judge = HttpJudge(
        _settings(jev_base_url="http://127.0.0.1:8080/", openrouter_api_key=None),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )
    result = judge.decide(_request())
    assert result.cost_usd is None
    answer = result.answers["is_bug"]
    assert isinstance(answer, NoulAnswer)
    assert answer.noul == pytest.approx(0.5)


def test_payment_required_is_not_retried() -> None:
    calls = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(402, json={"error": {"message": "Insufficient credits"}})

    judge = HttpJudge(
        _settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(JudgePaymentRequired, match="402"):
        judge.decide(_request())
    assert calls["count"] == 1


def test_rate_limit_retries_then_fails() -> None:
    calls = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(429, json={"error": {"message": "rate limit"}})

    judge = HttpJudge(
        _settings(jev_max_retries=2),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(JudgeRateLimited, match="429"):
        judge.decide(_request())
    assert calls["count"] == 3


def test_unexpected_answer_type_is_an_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"answers": {"is_bug": {"type": "box", "box": {}}}})

    judge = HttpJudge(
        _settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(JudgeResponseError, match="unexpected type"):
        judge.decide(_request())
