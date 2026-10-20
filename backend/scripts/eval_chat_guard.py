"""Compare Jev with the previous GPT data-guard classifiers. Do not call this from pytest.

The rows this script scores are text-to-SQL (Jev ``DATA_OFF_TOPIC`` versus the
old GPT off-topic prompt). Policy chat off-topic is not that comparison.
``evals.guard.pipeline`` follows ``graph.py``: regex, Jev injection, a short
message check, then GPT scope (``gpt_scope``).

``evals`` is not an installed package, so the backend root has to be on the
path before that import. Prefer ``uv run python -m evals.guard.live`` from
``backend/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from evals.guard.live import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
