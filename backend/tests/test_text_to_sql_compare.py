"""The pipeline comparison harness stays offline unless the bench is confirmed."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from evals.text_to_sql_pipeline import compare

_BACKEND = Path(__file__).resolve().parents[1]


def test_dry_run_prints_the_sample_and_makes_no_live_call() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "evals.text_to_sql_pipeline.compare", "--dry-run"],
        cwd=_BACKEND,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "sample-my-students" in completed.stdout
    assert "No calls made." in completed.stdout
    assert "models:" in completed.stdout
    assert "usd worst with transport retries:" in completed.stdout
    assert compare.main(["--live"]) == 2


def test_live_refuses_when_the_worst_case_exceeds_the_cap(monkeypatch) -> None:
    monkeypatch.setenv("TEXT_TO_SQL_BENCH_CONFIRM", "1")
    monkeypatch.setenv("TEXT_TO_SQL_BENCH_USER_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("TEXT_TO_SQL_BENCH_INSTITUTION_ID", "00000000-0000-0000-0000-000000000002")
    monkeypatch.setattr(
        compare, "_print_cost", lambda questions, max_usd: SimpleNamespace(worst_usd=9.0)
    )

    def _unexpected(questions: object) -> int:
        raise AssertionError("live pipelines must not run when the cap is exceeded")

    monkeypatch.setattr(compare, "run_live", _unexpected)
    assert compare.main(["--live", "--max-usd", "0.50"]) == 2
