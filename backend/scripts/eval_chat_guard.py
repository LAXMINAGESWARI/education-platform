"""Compare Jev with the previous GPT guard classifiers. Do not call this from pytest.

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
