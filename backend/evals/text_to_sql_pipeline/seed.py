"""Load Golden Bench School and write expected rows from the reference SQL.

No model is called. From ``backend/``::

    DATABASE_URL=postgresql+asyncpg://education:education@localhost:5432/education_test \\
    uv run python -m evals.text_to_sql_pipeline.seed --write-golden

The default database is whatever ``DATABASE_URL`` / ``backend/.env`` names. The
loader deletes only the Golden Bench School institution. Do not point it at
production. ``compare.py --live --load-fixture`` loads the same school without
rewriting the golden file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.orm import Session

from education_platform.db.session import sync_session
from evals.text_to_sql_pipeline.compare import DEFAULT_GOLDEN
from evals.text_to_sql_pipeline.fixture import BenchSchool, load_golden_bench, seed_school
from evals.text_to_sql_pipeline.references import Reference, references
from evals.text_to_sql_pipeline.rows import canonicalize

_HEADER = """# Golden Bench School expected rows.
# Regenerated with no model call:
#   uv run python -m evals.text_to_sql_pipeline.seed --write-golden
# Each expect_rows value is the reference SQL in
# evals/text_to_sql_pipeline/references.py run against that local fixture.
# compare.py --live --load-fixture loads the same school into DATABASE_URL.
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write-golden", action="store_true", help="rewrite the golden YAML")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    args = parser.parse_args(argv)
    if not args.write_golden:
        parser.error("pass --write-golden")
    return args


def execute_reference(
    session: Session, school: BenchSchool, spec: Reference
) -> list[dict[str, Any]]:
    if spec.sql is None:
        return []
    params: dict[str, Any] = {
        "current_user_id": school.teacher_user_id,
        "current_institution_id": school.institution_id,
        **spec.bound(),
    }
    session.execute(text("SET TIME ZONE 'UTC'"))
    result = session.execute(text(spec.sql), params)
    rows = [dict(row) for row in result.mappings()]
    canonical = canonicalize(rows)
    if not isinstance(canonical, list):
        raise TypeError(spec.id)
    return canonical


def question_document(spec: Reference, rows: list[dict[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": spec.id,
        "question": spec.question,
        "route": spec.route,
        "expect_rows": rows,
    }
    if spec.expect_intent is not None:
        document["expect_intent"] = spec.expect_intent
    if spec.expect_refused:
        document["expect_refused"] = True
    if spec.forbidden_names:
        document["forbidden_names"] = list(spec.forbidden_names)
    return document


def write_golden(path: Path, questions: list[dict[str, Any]]) -> None:
    body = {"version": 1, "fixture": "golden_bench_school", "questions": questions}
    dumped = yaml.safe_dump(body, sort_keys=False, allow_unicode=True)
    path.write_text(_HEADER + dumped, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    school = load_golden_bench()
    session = sync_session()
    try:
        questions = [
            question_document(spec, execute_reference(session, school, spec))
            for spec in references()
        ]
    finally:
        session.close()
    write_golden(args.golden, questions)
    print(
        f"wrote {args.golden}: {len(questions)} questions; "
        f"teacher={school.teacher_user_id} institution={school.institution_id}"
    )
    print("No model calls made.")
    return 0


def rows_for_session(session: Session, school: BenchSchool | None = None) -> list[dict[str, Any]]:
    """Used by tests that already hold a session. Does not commit."""
    seeded = school or seed_school(session)
    session.flush()
    return [
        question_document(spec, execute_reference(session, seeded, spec)) for spec in references()
    ]


if __name__ == "__main__":
    sys.exit(main())
