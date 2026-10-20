"""Run the Postgres ingest worker: ``python -m education_platform.workers``."""

from __future__ import annotations

from education_platform.modules.judge.policy import warn_judge_mode_at_startup
from education_platform.workers.runner import run_forever


def main() -> None:
    warn_judge_mode_at_startup()
    run_forever()


if __name__ == "__main__":
    main()
