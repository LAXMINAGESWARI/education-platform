"""Compare Jev with the previous GPT guard classifiers. Do not call this from pytest.

Policy chat follows ``graph.py``: regex, Jev injection, a short-message check,
then GPT scope (``gpt_scope``). Data off-topic stays a Jev noul. Paid calls
only with ``--live``; see ``evals/guard/README.md``. From ``backend/``::

    uv run python scripts/eval_chat_guard.py --dry-run
    uv run python scripts/eval_chat_guard.py --live --max-usd 2.00
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.guard.run import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
