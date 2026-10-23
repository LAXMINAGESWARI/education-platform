"""Build the synthetic Al Noor school for a load-test database.

Idempotent: generate_school() wipes and rebuilds that institution name.
Refuses to run unless DEMO_SEED=1, so a prod process cannot call it by accident.
The demo password is demo1234. It is not a secret.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.db.url import to_sync_url
from education_platform.modules.synthetic.generator import (
    DEFAULT_PASSWORD,
    SchoolSpec,
    generate_school,
)


def main() -> None:
    if os.environ.get("DEMO_SEED") != "1":
        print("DEMO_SEED=1 is required. Refusing to seed.", file=sys.stderr)
        raise SystemExit(2)

    settings = get_settings()
    spec = SchoolSpec()
    engine = create_engine(to_sync_url(settings.database_url), pool_pre_ping=True)
    with Session(engine) as session:
        result = generate_school(session, spec)
        session.commit()
    engine.dispose()
    print(f"institution={spec.institution_name}")
    print(f"students={result.students} teachers={result.teachers}")
    print(f"demo_password={DEFAULT_PASSWORD}")


if __name__ == "__main__":
    main()
