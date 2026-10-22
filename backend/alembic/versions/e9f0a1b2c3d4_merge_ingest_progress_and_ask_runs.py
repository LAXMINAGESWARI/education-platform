"""merge ingest progress and text-to-sql ask-run heads

Revision ID: e9f0a1b2c3d4
Revises: b4c5d6e7f8a9, d5e6f7a8b9c0
Create Date: 2026-10-22 23:30:00
"""

from collections.abc import Sequence

revision: str | Sequence[str] | None = "e9f0a1b2c3d4"
down_revision: str | Sequence[str] | None = ("b4c5d6e7f8a9", "d5e6f7a8b9c0")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
