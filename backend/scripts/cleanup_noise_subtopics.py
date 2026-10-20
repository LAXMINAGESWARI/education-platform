"""Merge stored textbook-heading subtopics into the concept they belong to.

Dry-run is the default so a checkout is not cleaned by accident. Pass ``--apply``
to write. ``--subject`` limits the scan to one subject id or name.

The database is never taken from ``DATABASE_URL`` or ``backend/.env``. Pass
``--database-url`` or set ``CLEANUP_DATABASE_URL``. ``--apply`` also requires
``--yes``, or a typed database-name confirmation when stdin is a terminal.

From ``backend/``::

    uv run python scripts/cleanup_noise_subtopics.py \\
        --database-url "$CLEANUP_DATABASE_URL"
    uv run python scripts/cleanup_noise_subtopics.py \\
        --database-url "$CLEANUP_DATABASE_URL" --apply --yes
"""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from education_platform.db.url import to_sync_url
from education_platform.modules.materials.noise_subtopics import cleanup_noise_subtopics


def resolve_database_url(explicit: str | None) -> str | None:
    """``--database-url`` or ``CLEANUP_DATABASE_URL``. Never ``DATABASE_URL``."""
    if explicit is not None and explicit.strip():
        return explicit.strip()
    env = os.environ.get("CLEANUP_DATABASE_URL", "").strip()
    if env:
        return env
    return None


def describe_database_target(url: str) -> str:
    """Host, port, and database name. The password is never printed."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = "" if parts.port is None else str(parts.port)
    name = parts.path.lstrip("/").split("?", 1)[0]
    return f"host={host} port={port} database={name} password=***"


def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/").split("?", 1)[0]


def confirm_apply(database: str, *, assume_yes: bool) -> bool:
    """``--yes`` or a typed database name. A non-interactive shell must pass ``--yes``."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(
            "Refusing to apply without --yes. Re-run with --yes to confirm.",
            file=sys.stderr,
        )
        return False
    typed = input(f"Type the database name to apply ({database}): ").strip()
    if typed != database:
        print(
            "Refusing to apply: confirmation did not match the database name.",
            file=sys.stderr,
        )
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the merge. Omit this to print counts and change nothing.",
    )
    parser.add_argument(
        "--subject",
        help="Only this subject, by id or by name. Omit to scan every subject.",
    )
    parser.add_argument(
        "--database-url",
        help="Database to clean. Overrides CLEANUP_DATABASE_URL. DATABASE_URL is ignored.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm --apply. Without this, a terminal must type the database name.",
    )
    args = parser.parse_args(argv)
    url = resolve_database_url(args.database_url)
    if url is None:
        print(
            "Refusing to run: pass --database-url or set CLEANUP_DATABASE_URL. "
            "This script does not read DATABASE_URL or backend/.env.",
            file=sys.stderr,
        )
        return 2
    print(describe_database_target(url))
    dry_run = not args.apply
    if not dry_run and not confirm_apply(database_name(url), assume_yes=args.yes):
        return 2
    engine = create_engine(to_sync_url(url))
    with Session(engine) as session:
        try:
            report = cleanup_noise_subtopics(session, dry_run=dry_run, subject=args.subject)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            session.rollback()
            engine.dispose()
            return 2
        if dry_run:
            session.rollback()
        else:
            session.commit()
    mode = "dry-run" if dry_run else "applied"
    print(f"mode: {mode}")
    print(report.render())
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
