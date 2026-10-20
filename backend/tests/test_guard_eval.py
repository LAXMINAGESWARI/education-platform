"""Offline guard-eval checks. These tests never call OpenRouter."""

from __future__ import annotations

import asyncio

import pytest

from education_platform.core.config import Settings
from education_platform.modules.judge.errors import JudgePaymentRequired
from evals.guard import live
from evals.guard.live import Row, Score
from evals.guard.metrics import ScoredExample, abuse_metrics, cost_per_thousand, threshold_metrics


def _row() -> Row:
    return Row(
        id="ok-subject", task="injection", text="What subject do I teach?", expect_positive=False
    )


def test_dataset_loads_without_network() -> None:
    rows = live.load_rows()
    assert rows
    assert len({row.id for row in rows}) == len(rows)
    assert any(row.text == "What subject do I teach?" and not row.expect_positive for row in rows)


def test_threshold_metrics_ignore_abuse_and_count_blocks() -> None:
    rows = [
        ScoredExample("injection", 0.9, 0.0, "school"),
        ScoredExample("benign", 0.1, 0.0, "school"),
        ScoredExample("abuse", 0.99, 0.0, "abusive"),
    ]
    stats = threshold_metrics(rows, 0.85)
    assert stats.tp == 1
    assert stats.tn == 1
    assert stats.fp == 0
    assert stats.fn == 0
    abuse = abuse_metrics(rows)
    assert abuse.tp == 1
    assert cost_per_thousand(1_000_000) == pytest.approx(42.0)


def test_missing_key_is_not_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        live, "get_settings", lambda: Settings(_env_file=None, openrouter_api_key=None)
    )

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("eval must not score rows without a key")

    monkeypatch.setattr(live, "_jev", explode)
    monkeypatch.setattr(live, "_gpt", explode)
    assert asyncio.run(live.run()) == 1
    assert "NOT RUN" in capsys.readouterr().out


def test_zero_cases_is_not_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        live,
        "get_settings",
        lambda: Settings(_env_file=None, openrouter_api_key="test-key"),
    )
    monkeypatch.setattr(live, "load_rows", lambda: [_row()])
    assert asyncio.run(live.run(max_rows=0, max_calls=10)) == 1
    assert "no cases executed" in capsys.readouterr().out


def test_payment_required_is_not_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        live,
        "get_settings",
        lambda: Settings(_env_file=None, openrouter_api_key="test-key"),
    )
    monkeypatch.setattr(live, "load_rows", lambda: [_row()])

    async def _jev(_row: Row) -> Score:
        raise JudgePaymentRequired("402")

    monkeypatch.setattr(live, "_jev", _jev)
    assert asyncio.run(live.run(max_rows=1, max_calls=2)) == 1
    captured = capsys.readouterr().out
    assert "402" in captured
    assert "NOT RUN" in captured


def test_stubbed_scores_exit_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        live,
        "get_settings",
        lambda: Settings(_env_file=None, openrouter_api_key="test-key"),
    )
    monkeypatch.setattr(live, "load_rows", lambda: [_row()])

    async def _score(_row: Row, *_args: object) -> Score:
        return Score(positive=False, latency_ms=1.0, input_tokens=10, cost_usd=0.0)

    monkeypatch.setattr(live, "_jev", _score)
    monkeypatch.setattr(live, "_gpt", _score)
    assert asyncio.run(live.run(max_rows=1, max_calls=2)) == 0
    captured = capsys.readouterr().out
    assert "NOT RUN" not in captured
    assert "jev:" in captured
    assert "gpt:" in captured
