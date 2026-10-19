"""Run the labelled chat-guard fixture live against Jev. Do not call this from pytest."""

from __future__ import annotations

from education_platform.modules.guard.eval_live import main

if __name__ == "__main__":
    raise SystemExit(main())
