"""Rebuild the synthetic school from one command.

    uv run python -m education_platform.modules.synthetic.cli
    uv run python -m education_platform.modules.synthetic.cli --students-per-section 30

Deterministic: the same seed always produces the same school, so a demo can be reset to a
known state.
"""

from __future__ import annotations

import argparse

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.db.url import to_sync_url
from education_platform.modules.synthetic.generator import SchoolSpec, generate_school


def main() -> None:
    # SchoolSpec uses slots, so read defaults from an instance rather than the class.
    defaults = SchoolSpec()

    parser = argparse.ArgumentParser(description="Generate the synthetic demo school.")
    parser.add_argument("--institution", default=defaults.institution_name)
    parser.add_argument("--sections-per-grade", type=int, default=defaults.sections_per_grade)
    parser.add_argument("--students-per-section", type=int, default=defaults.students_per_section)
    parser.add_argument("--term-weeks", type=int, default=defaults.term_weeks)
    parser.add_argument("--seed", type=int, default=defaults.seed)
    args = parser.parse_args()

    spec = SchoolSpec(
        institution_name=args.institution,
        sections_per_grade=args.sections_per_grade,
        students_per_section=args.students_per_section,
        term_weeks=args.term_weeks,
        seed=args.seed,
    )

    settings = get_settings()
    engine = create_engine(to_sync_url(settings.database_url), pool_pre_ping=True)

    with Session(engine) as session:
        result = generate_school(session, spec)
        session.commit()

    engine.dispose()

    print(f"Institution : {spec.institution_name}")
    print(f"Students    : {result.students}")
    print(f"Teachers    : {result.teachers}")
    print(f"Sections    : {result.sections}")
    print(f"Offerings   : {result.offerings}")
    print(f"Lessons     : {result.lessons}")
    print(f"Quiz attempts    : {result.attempts}")
    print(f"Attendance rows  : {result.attendance_rows}")
    print("Planted for the demo:")
    for key, value in result.planted.items():
        print(f"  - {key}: {value}")
    print(f"\nAll accounts use the password: {'demo1234'}")


if __name__ == "__main__":
    main()
