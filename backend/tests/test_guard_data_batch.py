"""The data-guard eval flag pairs injection and off-topic without changing the default."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from education_platform.core.config import Settings
from education_platform.modules.judge.types import DecisionResult, NoulAnswer
from evals.guard.collect import collect
from evals.guard.dataset import Row
from evals.guard.run import parse_args
from evals.guard.surfaces import CHECKS

_ROW = Row(
    id="r1",
    text="How many students do I teach?",
    label="benign",
    category="ok",
    surfaces=("data",),
)


def _jobs() -> list[Any]:
    checks = [check for check in CHECKS if check.key.startswith("data.jev_")]
    from evals.guard.collect import Job

    return [Job(check, _ROW) for check in checks]


def _settings() -> Settings:
    return Settings(_env_file=None, openrouter_api_key="test-key")


def _client() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_default_collect_calls_jev_once_per_check(monkeypatch: Any) -> None:
    calls: list[set[str]] = []

    async def post(**kwargs: Any) -> DecisionResult:
        calls.append(set(kwargs["questions"]))
        return DecisionResult(
            model="m",
            answers={"decision": NoulAnswer(0.1)},
            input_tokens=3,
            output_tokens=0,
            cost_usd=0.02,
            latency_ms=5,
            request_id=None,
            provider=None,
        )

    monkeypatch.setattr("evals.guard.collect.post_decisions", post)
    collection = await collect(_jobs(), _settings(), _client(), max_usd=1)
    assert calls == [{"decision"}, {"decision"}]
    assert len(collection.results) == 2
    assert sum(result.cost_usd for result in collection.results) == 0.04


async def test_data_guard_batch_is_one_request_and_charges_injection(monkeypatch: Any) -> None:
    calls: list[set[str]] = []

    async def post(**kwargs: Any) -> DecisionResult:
        calls.append(set(kwargs["questions"]))
        return DecisionResult(
            model="m",
            answers={"injection": NoulAnswer(0.1), "off_topic": NoulAnswer(0.2)},
            input_tokens=8,
            output_tokens=0,
            cost_usd=0.05,
            latency_ms=9,
            request_id=None,
            provider=None,
        )

    monkeypatch.setattr("evals.guard.collect.post_decisions", post)
    collection = await collect(_jobs(), _settings(), _client(), max_usd=1, data_guard_batch=True)
    assert calls == [{"injection", "off_topic"}]
    by_check = {result.check: result for result in collection.results}
    assert by_check["data.jev_injection"].cost_usd == 0.05
    assert by_check["data.jev_off_topic"].cost_usd == 0.0
    assert by_check["data.jev_injection"].latency_ms == by_check["data.jev_off_topic"].latency_ms
    assert by_check["data.jev_injection"].probability == 0.1
    assert by_check["data.jev_off_topic"].probability == 0.2
    assert collection.spent_usd == 0.05


def test_batch_flag_defaults_off_and_dry_run_mentions_it() -> None:
    assert parse_args(["--dry-run"]).data_guard_batch is False
    root = subprocess.run(
        [sys.executable, "scripts/eval_chat_guard.py", "--surfaces", "data", "--dry-run"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    batched = subprocess.run(
        [
            sys.executable,
            "scripts/eval_chat_guard.py",
            "--surfaces",
            "data",
            "--dry-run",
            "--data-guard-batch",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "data guard batch" not in root.stdout
    assert "one post_decisions per row" in batched.stdout
