"""Deploy routing and Compose budgets for the readiness probe."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from education_platform.core.config import Settings

_REPO = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO / "deploy" / "docker-compose.prod.yml"

# Cold start of the api image on a small VM: import the process, seed an empty
# curriculum, and answer the first probe. The worker in this compose file allows
# 180s before its checks count; the api window must cover at least that long.
_WORST_CASE_STARTUP_SECONDS = 180


def test_caddy_routes_health_subpaths() -> None:
    text = (_REPO / "deploy" / "Caddyfile.site").read_text(encoding="utf-8")
    assert "handle /health*" in text
    assert "handle /health {" not in text


def test_compose_api_healthcheck_targets_ready_within_budget() -> None:
    text = _COMPOSE.read_text(encoding="utf-8")
    api = text.split("  api:", maxsplit=1)[1].split("  worker:", maxsplit=1)[0]
    match = re.search(r"timeout:\s*(\d+)s", api)
    assert match is not None
    timeout = int(match.group(1))
    assert timeout > Settings(_env_file=None).health_ready_timeout_seconds


def _seconds(value: object) -> int:
    if isinstance(value, str):
        match = re.fullmatch(r"(\d+)s", value)
        if match is not None:
            return int(match.group(1))
    raise AssertionError(f"expected a compose duration in seconds, got {value!r}")


def _api_healthcheck() -> dict[str, object]:
    document = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    healthcheck = document["services"]["api"]["healthcheck"]
    assert isinstance(healthcheck, dict)
    return healthcheck


def test_compose_api_healthcheck_covers_startup_budget() -> None:
    healthcheck = _api_healthcheck()
    interval = _seconds(healthcheck["interval"])
    timeout = _seconds(healthcheck["timeout"])
    start_period = _seconds(healthcheck["start_period"])
    retries = healthcheck["retries"]
    assert isinstance(retries, int)
    assert start_period + interval * retries >= _WORST_CASE_STARTUP_SECONDS
    assert timeout >= Settings(_env_file=None).health_ready_timeout_seconds
