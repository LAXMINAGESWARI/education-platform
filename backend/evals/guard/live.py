"""Paid comparison of Jev against the previous GPT guard classifiers.

Uses the labelled rows in ``modules/guard/fixtures/golden_guard_rows.json``.
Does not run from pytest. From ``backend/``::

    uv run python -m evals.guard.live

``--max-rows`` (default 5) and ``--max-calls`` (default 10) cap the paid calls.
Each row costs one Jev call and one GPT call. A run that does not score any
rows prints ``NOT RUN`` and exits non-zero. CI must not invoke this module
against OpenRouter; ``tests/test_guard_eval.py`` stubs the calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from education_platform.core.config import get_settings
from education_platform.modules.guard.classifier import (
    DATA_GPT_INJECTION,
    DATA_GPT_OFF_TOPIC,
    DATA_INJECTION,
    DATA_OFF_TOPIC,
    guard_state,
    is_positive,
)
from education_platform.modules.judge.client import decisions_endpoint, post_decisions
from education_platform.modules.judge.errors import JudgeError, JudgePaymentRequired
from education_platform.modules.judge.types import NoulAnswer
from evals.guard.metrics import INPUT_USD_PER_MILLION, ThresholdStats

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "education_platform"
    / "modules"
    / "guard"
    / "fixtures"
    / "golden_guard_rows.json"
)


@dataclass(frozen=True, slots=True)
class Row:
    id: str
    task: str
    text: str
    expect_positive: bool


@dataclass
class Score:
    positive: bool
    latency_ms: float
    input_tokens: int | None
    cost_usd: float | None


def load_rows() -> list[Row]:
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return [
        Row(
            id=str(item["id"]),
            task=str(item["task"]),
            text=str(item["text"]),
            expect_positive=bool(item["expect_positive"]),
        )
        for item in raw
    ]


def _metrics(expect: list[bool], got: list[bool]) -> ThresholdStats:
    tp = sum(1 for wanted, seen in zip(expect, got, strict=True) if wanted and seen)
    fp = sum(1 for wanted, seen in zip(expect, got, strict=True) if not wanted and seen)
    fn = sum(1 for wanted, seen in zip(expect, got, strict=True) if wanted and not seen)
    tn = sum(1 for wanted, seen in zip(expect, got, strict=True) if not wanted and not seen)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return ThresholdStats(
        threshold=get_settings().jev_block_threshold,
        precision=precision,
        recall=recall,
        tp=tp,
        fp=fp,
        fn=fn,
        tn=tn,
    )


def _question(task: str) -> dict[str, Any]:
    if task == "off_topic":
        return DATA_OFF_TOPIC
    return DATA_INJECTION


def _not_run(reason: str) -> int:
    print(f"NOT RUN: {reason}")
    return 1


async def _jev(row: Row) -> Score:
    started = time.perf_counter()
    settings = get_settings()
    response = await post_decisions(
        state=guard_state(row.text),
        questions={"decision": _question(row.task)},
        settings=settings,
    )
    elapsed = (time.perf_counter() - started) * 1000
    answer = response.answers.get("decision")
    if not isinstance(answer, NoulAnswer):
        raise JudgeError("missing noul")
    return Score(
        positive=is_positive(answer.noul, settings),
        latency_ms=elapsed,
        input_tokens=response.input_tokens,
        cost_usd=response.cost_usd,
    )


async def _gpt(row: Row, client: httpx.AsyncClient) -> Score:
    settings = get_settings()
    prompt = DATA_GPT_OFF_TOPIC if row.task == "off_topic" else DATA_GPT_INJECTION
    key_name = "off_topic" if row.task == "off_topic" else "injection"
    started = time.perf_counter()
    response = await client.post(
        f"{settings.openrouter_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
        json={
            "model": settings.openrouter_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": row.text},
            ],
        },
    )
    elapsed = (time.perf_counter() - started) * 1000
    if response.status_code == 402:
        raise JudgePaymentRequired("OpenRouter returned 402 (insufficient credits)")
    response.raise_for_status()
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    prompt_tokens = usage.get("prompt_tokens")
    cost = usage.get("cost")
    return Score(
        positive=bool(parsed.get(key_name)),
        latency_ms=elapsed,
        input_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
        cost_usd=(
            float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
        ),
    )


def _print_block(name: str, rows: list[Row], scores: list[Score]) -> None:
    stats = _metrics([row.expect_positive for row in rows], [score.positive for score in scores])
    latencies = [score.latency_ms for score in scores]
    tokens = [score.input_tokens for score in scores if score.input_tokens is not None]
    costs = [score.cost_usd for score in scores if score.cost_usd is not None]
    mean_latency = sum(latencies) / len(latencies) if latencies else 0.0
    mean_tokens = sum(tokens) / len(tokens) if tokens else None
    print(
        f"{name}: n={len(rows)} precision={stats.precision:.3f} recall={stats.recall:.3f} "
        f"tp={stats.tp} fp={stats.fp} fn={stats.fn} tn={stats.tn} "
        f"mean_latency_ms={mean_latency:.0f}"
    )
    if mean_tokens is not None:
        print(f"  mean_input_tokens={mean_tokens:.1f}")
    if costs:
        mean_cost = sum(costs) / len(costs)
        print(f"  usage.cost_usd_per_1000={mean_cost * 1000:.6f}")


def _cap_rows(rows: list[Row], *, max_rows: int, max_calls: int) -> list[Row]:
    """Keep the spend bounded. Two calls (Jev and GPT) are made per row."""
    row_limit = max(0, max_rows)
    call_limit = max(0, max_calls) // 2
    return rows[: min(row_limit, call_limit)]


async def run(*, max_rows: int = 5, max_calls: int = 10) -> int:
    settings = get_settings()
    if not settings.openrouter_configured or not settings.openrouter_api_key:
        return _not_run("OPENROUTER_API_KEY is not set.")
    rows = _cap_rows(load_rows(), max_rows=max_rows, max_calls=max_calls)
    if not rows:
        return _not_run("no cases executed.")
    print(
        f"rows={len(rows)} jev={settings.resolved_guard_jev_model} "
        f"gpt={settings.openrouter_model} threshold={settings.jev_block_threshold} "
        f"decisions={decisions_endpoint(settings)}"
    )
    try:
        jev_scores = [await _jev(row) for row in rows]
    except JudgePaymentRequired:
        return _not_run("OpenRouter returned 402 (insufficient credits).")
    except JudgeError as exc:
        return _not_run(f"Jev failed: {exc}")
    _print_block("jev", rows, jev_scores)
    jev_tokens = [score.input_tokens for score in jev_scores if score.input_tokens is not None]
    if jev_tokens:
        mean_tokens = sum(jev_tokens) / len(jev_tokens)
        per_thousand = mean_tokens * 1000 / 1_000_000 * INPUT_USD_PER_MILLION
        print(f"jev list_price_usd_per_1000={per_thousand:.6f} at ${INPUT_USD_PER_MILLION}/M input")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            gpt_scores = [await _gpt(row, client) for row in rows]
    except JudgePaymentRequired:
        return _not_run("OpenRouter returned 402 (insufficient credits) on the GPT classifier.")
    except (JudgeError, httpx.HTTPError) as exc:
        return _not_run(f"GPT classifier failed: {exc}")
    _print_block("gpt", rows, gpt_scores)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Jev versus GPT guard comparison.")
    parser.add_argument("--max-rows", type=int, default=5)
    parser.add_argument("--max-calls", type=int, default=10)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(max_rows=args.max_rows, max_calls=args.max_calls)))
