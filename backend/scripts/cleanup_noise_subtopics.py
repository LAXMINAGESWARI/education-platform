"""Merge stored textbook-heading subtopics into the concept they belong to.

Dry-run is the default so a checkout is not cleaned by accident. Pass ``--apply``
to write. ``--subject`` limits the scan to one subject id or name. The script
uses ``DATABASE_URL`` from the backend settings. Do not point that at production.

From ``backend/``::

    uv run python scripts/cleanup_noise_subtopics.py
    uv run python scripts/cleanup_noise_subtopics.py --subject Mathematics
    uv run python scripts/cleanup_noise_subtopics.py --subject Mathematics --apply
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.db.url import to_sync_url
from education_platform.modules.materials.noise_subtopics import cleanup_noise_subtopics


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
    args = parser.parse_args(argv)
    dry_run = not args.apply
    engine = create_engine(to_sync_url(get_settings().database_url))
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
