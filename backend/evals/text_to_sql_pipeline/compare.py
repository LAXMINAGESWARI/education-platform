"""Compare the legacy graph and the lean text-to-SQL pipeline on one golden file.

``--dry-run`` prints the questions, a token and USD estimate, and the metrics a
live run would report. It reads local prompt files and GETs the public
OpenRouter model list. It does not call a model and it does not open a database.

From ``backend/``::

    uv run python -m evals.text_to_sql_pipeline.compare --dry-run

A live run needs ``TEXT_TO_SQL_BENCH_CONFIRM=1``. ``--load-fixture`` loads
Golden Bench School into ``DATABASE_URL`` and fills the teacher and institution
ids. Without that flag, set ``TEXT_TO_SQL_BENCH_USER_ID`` and
``TEXT_TO_SQL_BENCH_INSTITUTION_ID``. See ``README.md`` in this directory.
``--max-usd`` refuses the live run before any model call when the worst-case
estimate (both pipelines, pipeline retries, and transport retries) is over the cap.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from education_platform.core.config import Settings
from evals.pricing import load_prices
from evals.text_to_sql_pipeline.cost import estimate, render
from evals.text_to_sql_pipeline.fixture import load_golden_bench
from evals.text_to_sql_pipeline.replay import run_replay
from evals.text_to_sql_pipeline.rows import canonicalize

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

DEFAULT_GOLDEN = _BACKEND / "tests" / "fixtures" / "text_to_sql_golden.yaml"
DEFAULT_REPLAY = _BACKEND / "tests" / "fixtures" / "text_to_sql_replay.yaml"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--dry-run", action="store_true", help="print the plan; no model calls")
    parser.add_argument("--live", action="store_true", help="run both pipelines locally")
    parser.add_argument(
        "--replay",
        action="store_true",
        help="run both pipelines on recorded responses; no network",
    )
    parser.add_argument("--replay-file", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument(
        "--load-fixture",
        action="store_true",
        help="load Golden Bench School into DATABASE_URL before a live run",
    )
    parser.add_argument("--max-usd", type=float, default=2.00)
    args = parser.parse_args(argv)
    if sum((args.live, args.dry_run, args.replay)) > 1:
        parser.error("pass only one of --live, --dry-run, and --replay")
    if args.max_usd <= 0:
        parser.error("--max-usd must be positive")
    if args.load_fixture and not args.live:
        parser.error("--load-fixture is only valid with --live")
    return args


def load_questions(path: Path) -> list[dict[str, Any]]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not isinstance(loaded.get("questions"), list):
        raise ValueError(f"{path} must contain a questions list")
    questions: list[dict[str, Any]] = []
    for item in loaded["questions"]:
        if not isinstance(item, dict) or not isinstance(item.get("question"), str):
            raise ValueError(f"each golden question needs a question string: {item!r}")
        questions.append(item)
    if not questions:
        raise ValueError(f"{path} has no questions")
    return questions


def _percentile(values: list[int], percent: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(percent / 100 * len(ordered)) - 1)
    return ordered[index]


def _normalise(sql: str) -> str:
    return " ".join(sql.split()).lower()


def _names_hidden(spec: dict[str, Any], result: dict[str, Any]) -> bool:
    names = spec.get("forbidden_names") or []
    if not isinstance(names, list) or not names:
        return True
    blob = str(canonicalize(result.get("query_result") or []))
    blob += "\n" + str(result.get("natural_answer") or "")
    return not any(isinstance(name, str) and name in blob for name in names)


def _matches(spec: dict[str, Any], result: dict[str, Any]) -> bool | None:
    """``None`` when this row is scored by agreement with the other pipeline."""
    if spec.get("expect_refused"):
        rows = result.get("query_result") or []
        return bool(result.get("error")) and canonicalize(rows) == []
    if "expect_rows" in spec:
        same = canonicalize(result.get("query_result") or []) == canonicalize(spec["expect_rows"])
        return same and _names_hidden(spec, result)
    expect_sql = spec.get("expect_sql")
    if isinstance(expect_sql, str) and expect_sql.strip():
        return _normalise(expect_sql) in _normalise(result.get("validated_sql") or "")
    return None


def print_plan(path: Path, questions: list[dict[str, Any]]) -> None:
    print(f"golden {path}: {len(questions)} questions")
    for item in questions:
        label = item.get("id") or item["question"]
        print(f"  {label}")
    print(
        "metrics: accuracy (expect_rows, else expect_sql, else row agreement), "
        "p50/p95 answer_ms, ttft_ms, llm_calls, prompt_tokens, completion_tokens"
    )
    print("legacy ttft_ms equals answer_ms; the lean stream records them separately")


def _confirmed() -> str | None:
    if os.environ.get("TEXT_TO_SQL_BENCH_CONFIRM") != "1":
        return "TEXT_TO_SQL_BENCH_CONFIRM=1"
    if not os.environ.get("TEXT_TO_SQL_BENCH_USER_ID"):
        return "TEXT_TO_SQL_BENCH_USER_ID"
    if not os.environ.get("TEXT_TO_SQL_BENCH_INSTITUTION_ID"):
        return "TEXT_TO_SQL_BENCH_INSTITUTION_ID"
    return None


def _base_state(question: str) -> dict[str, Any]:
    return {
        "question": question,
        "user_id": os.environ["TEXT_TO_SQL_BENCH_USER_ID"],
        "user_role": "teacher",
        "institution_id": os.environ["TEXT_TO_SQL_BENCH_INSTITUTION_ID"],
        "schema_context": "",
        "query_source": None,
        "generated_sql": None,
        "validated_sql": None,
        "retry_count": 0,
        "query_result": None,
        "result_row_count": None,
        "natural_answer": None,
        "confidence": None,
        "provenance": None,
        "error": None,
        "audit_entry": None,
    }


async def _run_legacy(question: str) -> tuple[dict[str, Any], dict[str, int]]:
    from education_platform.modules.text_to_sql.graph import build_text_to_sql_graph
    from education_platform.modules.text_to_sql.meter import meter_payload, reset_meter, start_meter

    graph = build_text_to_sql_graph()
    state = {**_base_state(question), "pipeline": "legacy"}
    token = start_meter()
    started = time.perf_counter()
    try:
        result = await graph.ainvoke(state, config={"recursion_limit": 25})
        elapsed = int((time.perf_counter() - started) * 1000)
        metrics = meter_payload(int(result.get("retry_count") or 0))
    finally:
        reset_meter(token)
    metrics["answer_ms"] = elapsed
    metrics["ttft_ms"] = elapsed
    return result, metrics


async def _run_lean(question: str) -> tuple[dict[str, Any], dict[str, int]]:
    from education_platform.modules.text_to_sql.lean import run_lean

    started = time.perf_counter()
    result = await run_lean(_base_state(question))
    elapsed = int((time.perf_counter() - started) * 1000)
    metrics = dict(result.get("pipeline_metrics") or {})
    metrics["answer_ms"] = elapsed
    # The one-shot runner has no earlier byte. The stream measurement replaces this.
    metrics.setdefault("ttft_ms", elapsed)
    return result, metrics


def _print_report(
    questions: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> None:
    legacy_hits = 0
    lean_hits = 0
    scored = 0
    agreements = 0
    for spec, row in zip(questions, rows, strict=True):
        legacy_match = _matches(spec, row["legacy"])
        lean_match = _matches(spec, row["lean"])
        if legacy_match is None or lean_match is None:
            same = (row["legacy"].get("query_result") or []) == (
                row["lean"].get("query_result") or []
            )
            agreements += int(same)
            legacy_match = same
            lean_match = same
        else:
            scored += 1
        legacy_hits += int(legacy_match)
        lean_hits += int(lean_match)
        print(
            f"{spec.get('id', spec['question'])}: "
            f"legacy_ok={bool(legacy_match)} lean_ok={bool(lean_match)} "
            f"legacy_ms={row['legacy_metrics'].get('answer_ms', 0)} "
            f"lean_ms={row['lean_metrics'].get('answer_ms', 0)} "
            f"legacy_llm={row['legacy_metrics'].get('llm_calls', 0)} "
            f"lean_llm={row['lean_metrics'].get('llm_calls', 0)} "
            f"legacy_ttft={row['legacy_metrics'].get('ttft_ms', 0)} "
            f"lean_ttft={row['lean_metrics'].get('ttft_ms', 0)}"
        )
    total = len(rows)
    print(f"accuracy legacy {legacy_hits}/{total} lean {lean_hits}/{total}")
    if scored == 0:
        print(f"row agreement {agreements}/{total}")
    for name, key in (("legacy", "legacy_metrics"), ("lean", "lean_metrics")):
        answer = [int(row[key].get("answer_ms") or 0) for row in rows]
        calls = [int(row[key].get("llm_calls") or 0) for row in rows]
        prompt = sum(int(row[key].get("prompt_tokens") or 0) for row in rows)
        completion = sum(int(row[key].get("completion_tokens") or 0) for row in rows)
        print(
            f"{name}: p50={_percentile(answer, 50)}ms p95={_percentile(answer, 95)}ms "
            f"llm_calls={calls} prompt_tokens={prompt} completion_tokens={completion}"
        )


async def run_live(questions: list[dict[str, Any]]) -> int:
    rows: list[dict[str, Any]] = []
    for spec in questions:
        legacy, legacy_metrics = await _run_legacy(spec["question"])
        lean, lean_metrics = await _run_lean(spec["question"])
        rows.append(
            {
                "legacy": legacy,
                "lean": lean,
                "legacy_metrics": legacy_metrics,
                "lean_metrics": lean_metrics,
            }
        )
    _print_report(questions, rows)
    return 0


def _print_cost(questions: list[dict[str, Any]], max_usd: float) -> Any:
    settings = Settings(_env_file=None, openrouter_api_key=None)  # type: ignore[call-arg]
    model_ids = [
        settings.openrouter_model.removeprefix("openrouter/"),
        settings.resolved_guard_jev_model,
    ]
    prices, note = load_prices(model_ids)
    report = estimate(questions, settings, prices, note)
    print(render(report))
    print(f"cap: ${max_usd:.2f}")
    if report.worst_usd > max_usd:
        print(
            f"WARNING: worst-case ${report.worst_usd:.6f} exceeds --max-usd "
            f"${max_usd:.2f}. A live run would refuse before any model call."
        )
    return report


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.replay:
        return run_replay(args.replay_file)
    questions = load_questions(args.golden)
    if not args.live:
        print_plan(args.golden, questions)
        _print_cost(questions, args.max_usd)
        print("No calls made.")
        return 0
    if os.environ.get("TEXT_TO_SQL_BENCH_CONFIRM") != "1":
        print(
            "Refusing live run: set TEXT_TO_SQL_BENCH_CONFIRM=1. "
            "Also set TEXT_TO_SQL_BENCH_USER_ID and TEXT_TO_SQL_BENCH_INSTITUTION_ID, "
            "or pass --load-fixture. Do not point this at production."
        )
        return 2
    if args.load_fixture:
        school = load_golden_bench()
        os.environ["TEXT_TO_SQL_BENCH_USER_ID"] = str(school.teacher_user_id)
        os.environ["TEXT_TO_SQL_BENCH_INSTITUTION_ID"] = str(school.institution_id)
        print(
            f"loaded Golden Bench School teacher={school.teacher_user_id} "
            f"institution={school.institution_id}"
        )
    missing = _confirmed()
    if missing is not None:
        print(
            f"Refusing live run: set {missing}. "
            "Also set TEXT_TO_SQL_BENCH_CONFIRM=1, TEXT_TO_SQL_BENCH_USER_ID, and "
            "TEXT_TO_SQL_BENCH_INSTITUTION_ID, or pass --load-fixture. "
            "Do not point this at production."
        )
        return 2
    report = _print_cost(questions, args.max_usd)
    if report.worst_usd > args.max_usd:
        print(
            f"Refusing live run: worst-case estimate ${report.worst_usd:.6f} "
            f"exceeds --max-usd ${args.max_usd:.2f}. No model was called."
        )
        return 2
    return asyncio.run(run_live(questions))


if __name__ == "__main__":
    sys.exit(main())
