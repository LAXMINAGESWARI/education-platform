"""Open DATABASE_URL and run SELECT 1. Worker container healthcheck."""

from __future__ import annotations

import os
import sys

import psycopg


def _libpq_url(url: str) -> str:
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


def main() -> None:
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(_libpq_url(url), connect_timeout=5) as conn:
        conn.execute("SELECT 1")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
