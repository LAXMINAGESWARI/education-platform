"""Unique dedupe key for progress subscribe audits.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-10-20 12:20:00.000000

Concurrent subscribes insert the same key. ON CONFLICT DO NOTHING keeps one row.
Other audit events leave the column null, and Postgres allows many nulls.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str | Sequence[str] | None = "d5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("dedupe_key", sa.String(length=400), nullable=True))
    op.create_index(
        "uq_audit_events_dedupe_key",
        "audit_events",
        ["dedupe_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_audit_events_dedupe_key", table_name="audit_events")
    op.drop_column("audit_events", "dedupe_key")
