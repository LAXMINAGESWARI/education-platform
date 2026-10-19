"""Audit quiz items and optionally archive the bad ones.

    python -m education_platform.scripts.audit_quiz_items --dry-run
    python -m education_platform.scripts.audit_quiz_items --retire

``--retire`` sets ``question_versions.lifecycle_status`` to archived. It does
not delete quiz items, attempt questions, or the version rows.
"""

from __future__ import annotations

import argparse

from education_platform.db.session import sync_session
from education_platform.modules.assessments.quiz_audit import audit_quiz_items, retire_flagged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit quiz items for bad or duplicate stems.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print invalid items and duplicate groups without changing them.",
    )
    parser.add_argument(
        "--retire",
        action="store_true",
        help="Archive invalid versions and extra copies. Keeps quiz and attempt rows.",
    )
    args = parser.parse_args(argv)
    session = sync_session()
    try:
        report = audit_quiz_items(session)
        print(f"invalid {len(report.invalid)}")
        for item in report.invalid:
            print(f"  {item.question_version_id} {item.status.value}: {'; '.join(item.reasons)}")
            print(f"    {item.prompt[:160]}")
        print(f"duplicate_groups {len(report.duplicate_groups)}")
        for index, group in enumerate(report.duplicate_groups, start=1):
            print(f"  group {index}")
            for item in group:
                print(f"    {item.question_version_id} {item.status.value}: {item.prompt[:160]}")
        if args.retire:
            changed = retire_flagged(session, report)
            session.commit()
            print(f"archived {changed}")
        else:
            print("dry-run (pass --retire to archive)")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
