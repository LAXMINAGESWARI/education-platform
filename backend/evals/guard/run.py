"""Chat-guard eval CLI: Jev vs the legacy GPT classifiers, with the regex stage.

Paid calls happen only with ``--live``. Without it (or with ``--dry-run``) the command
prints row counts and the estimated cost and makes no network call. From ``backend/``::

    uv run python scripts/eval_chat_guard.py --dry-run
    uv run python scripts/eval_chat_guard.py --live --max-usd 2.00
    uv run python scripts/eval_chat_guard.py --rescore evals/guard/results/<run>/raw_scores.jsonl

The key comes from ``OPENROUTER_API_KEY`` (environment or ``backend/.env``) via Settings,
only in ``--live`` mode. It is never printed or written.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from education_platform.core.config import Settings, get_settings
from evals.guard.collect import collect, estimate, plan_jobs
from evals.guard.dataset import DEFAULT_DATASET, SURFACES, Dataset, load_dataset
from evals.guard.score import baseline_block, compare_baseline, render_markdown, score_all
from evals.guard.surfaces import checks_for

RESULTS_DIR = Path(__file__).resolve().parent / "results"
EXIT_OK, EXIT_ABORTED, EXIT_REGRESSION = 0, 2, 3


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print rows and cost; no network")
    mode.add_argument("--live", action="store_true", help="make the paid OpenRouter calls")
    mode.add_argument("--rescore", type=Path, help="score a saved raw_scores.jsonl offline")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--xlsx", type=Path, help="Text-to-SQL-Golden-Eval-Set-50_Set-2.xlsx")
    parser.add_argument("--xlsx-off-topic-ids", default="", help="comma list of xlsx ids")
    parser.add_argument("--surfaces", default=",".join(SURFACES))
    parser.add_argument("--no-gpt", action="store_true", help="Jev only, skip legacy GPT")
    parser.add_argument("--max-usd", type=float, default=2.00)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--threshold", type=float, help="default: GUARD_JEV_BLOCK_THRESHOLD (0.6)")
    parser.add_argument("--target-fpr", type=float, default=0.01)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--baseline", type=Path, help="results.json or baseline json to compare")
    args = parser.parse_args(argv)
    surfaces = [s.strip() for s in args.surfaces.split(",") if s.strip()]
    if not surfaces or set(surfaces) - set(SURFACES):
        parser.error(f"--surfaces must be a subset of {','.join(SURFACES)}")
    args.surfaces = surfaces
    if args.max_usd <= 0:
        parser.error("--max-usd must be positive")
    return args


def offline_settings() -> Settings:
    """Model names and threshold without loading backend/.env or any key."""
    return Settings(_env_file=None, openrouter_api_key=None)  # type: ignore[call-arg]


def _dataset_summary(dataset: Dataset) -> dict[str, Any]:
    return {
        "name": dataset.name,
        "version": dataset.version,
        "sha256": dataset.sha256,
        "rows": len(dataset.rows),
        "by_label": dict(Counter(r.label for r in dataset.rows)),
        "by_category": dict(sorted(Counter(r.category for r in dataset.rows).items())),
        "sources": dataset.sources,
    }


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def print_plan(dataset: Dataset, args: argparse.Namespace, settings: Settings) -> None:
    jobs = plan_jobs(dataset.rows, checks_for(args.surfaces, include_gpt=not args.no_gpt))
    est = estimate(jobs, settings, concurrency=args.concurrency)
    summary = _dataset_summary(dataset)
    print(f"dataset {summary['name']} v{summary['version']}: {summary['rows']} rows")
    print(f"  by label:    {summary['by_label']}")
    print(f"  by category: {summary['by_category']}")
    print(f"  sources:     {summary['sources']}")
    per_check = Counter(job.check.key for job in jobs)
    print(f"surfaces {args.surfaces}; calls per check: {dict(per_check)}")
    print(
        f"models: jev={settings.resolved_guard_jev_model} gpt={settings.openrouter_model}"
        + ("" if args.no_gpt else ("" if est.gpt_price_known else " (unknown GPT price: "
                                   "pessimistic $5/$15 per M assumed)"))
    )  # fmt: skip
    for kind in sorted(est.calls):
        print(f"  {kind}: {est.calls[kind]} calls, est ${est.usd[kind]:.4f}")
    print(
        f"total: {est.total_calls} calls, est ${est.total_usd:.4f} "
        f"(cap ${args.max_usd:.2f}, reserve per call 1.5x estimate); "
        f"est runtime {est.runtime_seconds / 60:.1f} min at concurrency {args.concurrency}"
    )
    if est.total_usd * 1.5 > args.max_usd:
        print("WARNING: the cap is likely to stop the run before it finishes.")


def write_report(
    dataset: Dataset,
    raw: list[dict[str, Any]],
    args: argparse.Namespace,
    settings: Settings,
    run_info: dict[str, Any],
    out_dir: Path,
) -> int:
    threshold = args.threshold if args.threshold is not None else settings.guard_jev_block_threshold
    scored = score_all(
        dataset.rows,
        raw,
        args.surfaces,
        configured=threshold,
        target_fpr=args.target_fpr,
        include_gpt=not args.no_gpt,
    )
    baseline = baseline_block(scored)
    report: dict[str, Any] = {
        "schema": "chat_guard_eval/1",
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": _git_sha(),
        "dataset": _dataset_summary(dataset),
        "models": {
            "jev": settings.resolved_guard_jev_model,
            "gpt": None if args.no_gpt else settings.openrouter_model,
        },
        "configured_threshold": threshold,
        "target_fpr": args.target_fpr,
        "run": run_info,
        "surfaces": scored,
        "baseline": baseline,
        "baseline_regressions": None,
    }
    exit_code = EXIT_ABORTED if run_info.get("aborted") else EXIT_OK
    if args.baseline is not None:
        previous = json.loads(args.baseline.read_text(encoding="utf-8"))
        previous = previous.get("baseline", previous)
        regressions = compare_baseline(baseline, previous)
        report["baseline_regressions"] = regressions
        if regressions and exit_code == EXIT_OK:
            exit_code = EXIT_REGRESSION
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    (out_dir / "results.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"wrote {out_dir / 'results.json'} and {out_dir / 'results.md'}")
    for surface, entry in baseline.items():
        print(f"  {surface}: {entry}")
    return exit_code


async def run_live(
    dataset: Dataset,
    args: argparse.Namespace,
    settings: Settings,
    out_dir: Path,
    *,
    client: httpx.AsyncClient | None = None,
) -> int:
    jobs = plan_jobs(dataset.rows, checks_for(args.surfaces, include_gpt=not args.no_gpt))
    owns = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        collection = await collect(
            jobs,
            settings,
            http,
            max_usd=args.max_usd,
            concurrency=args.concurrency,
            progress=True,
        )
    finally:
        if owns:
            await http.aclose()
    raw = [result.to_json() for result in collection.results]
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "raw_scores.jsonl").open("w", encoding="utf-8") as handle:
        for item in raw:
            handle.write(json.dumps(item) + "\n")
    run_info = {
        "mode": "live",
        "max_usd": args.max_usd,
        "spent_usd": round(collection.spent_usd, 6),
        "calls": len(raw),
        "errors": sum(1 for item in raw if item["error"] is not None),
        "skipped_jobs": collection.skipped_jobs,
        "aborted": collection.aborted,
    }
    if collection.aborted:
        print(f"ABORTED: {collection.aborted}. Partial results follow.")
    return write_report(dataset, raw, args, settings, run_info, out_dir)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    off_topic_ids = frozenset(int(x) for x in args.xlsx_off_topic_ids.split(",") if x.strip())
    dataset = load_dataset(args.dataset, xlsx=args.xlsx, xlsx_off_topic_ids=off_topic_ids)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out_dir or RESULTS_DIR / stamp

    if args.rescore is not None:
        settings = offline_settings()
        raw = [json.loads(line) for line in args.rescore.read_text().splitlines() if line.strip()]
        run_info = {"mode": "rescore", "source": str(args.rescore), "max_usd": args.max_usd,
                    "spent_usd": 0.0, "calls": len(raw),
                    "errors": sum(1 for r in raw if r.get("error") is not None),
                    "skipped_jobs": 0, "aborted": None}  # fmt: skip
        return write_report(dataset, raw, args, settings, run_info, out_dir)

    if not args.live:
        print_plan(dataset, args, offline_settings())
        if not args.dry_run:
            print("No calls made. Add --live to run the paid eval.")
        return EXIT_OK

    settings = get_settings()
    if not settings.openrouter_configured:
        print("OPENROUTER_API_KEY is not set (env or backend/.env). Nothing was called.")
        return EXIT_ABORTED
    print_plan(dataset, args, settings)
    return asyncio.run(run_live(dataset, args, settings, out_dir))


if __name__ == "__main__":
    sys.exit(main())
