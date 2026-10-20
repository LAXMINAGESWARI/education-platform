"""The pipeline comparison harness stays offline unless the bench is confirmed."""

from __future__ import annotations

import subprocess
import sys

from evals.text_to_sql_pipeline.compare import main


def test_dry_run_prints_the_sample_and_makes_no_live_call() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "evals.text_to_sql_pipeline.compare", "--dry-run"],
        cwd="/workspace/backend",
        check=True,
        capture_output=True,
        text=True,
    )
    assert "sample-my-students" in completed.stdout
    assert "No calls made." in completed.stdout
    assert main(["--live"]) == 2
