"""Deploy routing and Compose budgets for the readiness probe."""

from __future__ import annotations

import re
from pathlib import Path

from education_platform.core.config import Settings

_REPO = Path(__file__).resolve().parents[2]


def test_caddy_routes_health_subpaths() -> None:
    text = (_REPO / "deploy" / "Caddyfile.site").read_text(encoding="utf-8")
    assert "handle /health*" in text
    assert "handle /health {" not in text


def test_compose_api_healthcheck_targets_ready_within_budget() -> None:
    text = (_REPO / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    api = text.split("  api:", maxsplit=1)[1].split("  worker:", maxsplit=1)[0]
    match = re.search(r"timeout:\s*(\d+)s", api)
    assert match is not None
    timeout = int(match.group(1))
    assert timeout > Settings(_env_file=None).health_ready_timeout_seconds
