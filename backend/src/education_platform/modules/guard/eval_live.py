"""Live precision/recall against TypeSafe Jev. Not imported by tests.

Run from ``backend/``::

    uv run python scripts/eval_chat_guard.py

Prints aggregate scores only. Never prints the API key.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from education_platform.core.config import get_settings
from education_platform.modules.guard.jev_client import JevError, post_decisions
from education_platform.modules.guard.metrics import (
    ScoredExample,
    abuse_metrics,
    cost_per_thousand,
    threshold_metrics,
)
from education_platform.modules.guard.policy import guard_questions, guard_state
from education_platform.modules.guard.service_types import GuardTurn

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "chat_guard_labels.json"
_THRESHOLDS = (0.5, 0.7, 0.85)


def load_fixture(path: Path = FIXTURE_PATH) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("chat guard fixture must be a list")
    return [row for row in payload if isinstance(row, dict)]


def _redact(text: str) -> str:
    if "Bearer " in text or "sk-or-" in text:
        return "redacted"
    return " ".join(text.split())[:240]


async def _score_one(row: dict[str, Any]) -> tuple[ScoredExample, int, int | None, float | None]:
    history_raw = row.get("history")
    history: list[GuardTurn] = []
    if isinstance(history_raw, list):
        for turn in history_raw:
            if not isinstance(turn, dict):
                continue
            role = turn.get("role")
            content = turn.get("content")
            if isinstance(role, str) and isinstance(content, str):
                history.append(GuardTurn(role=role, content=content))
    message = row.get("message")
    role = row.get("role")
    category = row.get("category")
    if not isinstance(message, str) or not isinstance(role, str) or not isinstance(category, str):
        raise ValueError(f"fixture row {row.get('id')!s} is missing message, role, or category")
    started = time.perf_counter()
    response = await post_decisions(
        state=guard_state(message=message, role=role, history=history),
        questions=guard_questions(),
        settings=get_settings(),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    answers = response.answers
    injection = answers.get("injection")
    exfiltration = answers.get("data_exfiltration")
    topic = answers.get("topic")
    if (
        not isinstance(injection, dict)
        or not isinstance(exfiltration, dict)
        or not isinstance(topic, dict)
    ):
        raise JevError("live eval response was missing an answer")
    injection_p = injection.get("noul")
    exfiltration_p = exfiltration.get("noul")
    choice = topic.get("choice")
    if (
        isinstance(injection_p, bool)
        or not isinstance(injection_p, (int, float))
        or isinstance(exfiltration_p, bool)
        or not isinstance(exfiltration_p, (int, float))
        or not isinstance(choice, str)
    ):
        raise JevError("live eval response had an unexpected answer shape")
    scored = ScoredExample(
        category=category,
        injection=float(injection_p),
        exfiltration=float(exfiltration_p),
        topic=choice,
    )
    return scored, latency_ms, response.input_tokens, response.cost_usd


def _print_stats(title: str, rows: list[ScoredExample]) -> None:
    print(title)
    header = (
        f"{'threshold':>10} {'precision':>10} {'recall':>10} "
        f"{'tp':>4} {'fp':>4} {'fn':>4} {'tn':>4}"
    )
    print(header)
    for threshold in _THRESHOLDS:
        stats = threshold_metrics(rows, threshold)
        print(
            f"{stats.threshold:10.2f} {stats.precision:10.3f} {stats.recall:10.3f} "
            f"{stats.tp:4d} {stats.fp:4d} {stats.fn:4d} {stats.tn:4d}"
        )


async def run() -> int:
    settings = get_settings()
    if not settings.openrouter_configured:
        print("OPENROUTER_API_KEY is not set. Live eval skipped.")
        return 0
    rows = load_fixture()
    attacks = sum(1 for row in rows if row.get("label") == "attack")
    benign = sum(1 for row in rows if row.get("label") == "benign")
    print(f"fixture={len(rows)} attacks={attacks} benign={benign} model={settings.jev_model}")
    scored: list[ScoredExample] = []
    latencies: list[int] = []
    input_tokens: list[int] = []
    costs: list[float] = []
    for row in rows:
        row_id = row.get("id")
        try:
            example, latency_ms, tokens, cost = await _score_one(row)
        except JevError as exc:
            if exc.status_code == 402:
                print("OpenRouter returned 402 (insufficient credits). Live eval stopped.")
                return 0
            print(f"error id={row_id} {_redact(str(exc))}")
            return 1
        scored.append(example)
        latencies.append(latency_ms)
        if tokens is not None:
            input_tokens.append(tokens)
        if cost is not None:
            costs.append(cost)
        print(
            f"{row_id} category={example.category} injection={example.injection:.2f} "
            f"exfiltration={example.exfiltration:.2f} topic={example.topic} latency_ms={latency_ms}"
        )
    _print_stats("injection_or_exfiltration (abuse rows excluded)", scored)
    abuse = abuse_metrics(scored)
    print(
        f"abuse_choice precision={abuse.precision:.3f} recall={abuse.recall:.3f} "
        f"tp={abuse.tp} fp={abuse.fp} fn={abuse.fn}"
    )
    mean_latency = sum(latencies) / len(latencies) if latencies else 0.0
    print(f"mean_latency_ms={mean_latency:.1f} n={len(latencies)}")
    if input_tokens:
        mean_tokens = sum(input_tokens) / len(input_tokens)
        print(
            f"mean_input_tokens={mean_tokens:.1f} "
            f"list_price_usd_per_1000={cost_per_thousand(mean_tokens):.6f}"
        )
    if costs:
        print(f"usage_cost_usd_per_1000={sum(costs) / len(costs) * 1000:.6f}")
    return 0


def main() -> int:
    return asyncio.run(run())
