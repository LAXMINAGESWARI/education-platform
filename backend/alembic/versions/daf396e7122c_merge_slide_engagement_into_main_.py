"""merge slide_engagement into main migration chain

Revision ID: daf396e7122c
Revises: 62bb8aab01db, a0b1c2d3e4f5
Create Date: 2026-10-17 01:29:18.734188
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str | Sequence[str] | None = "daf396e7122c"
down_revision: str | Sequence[str] | None = ("62bb8aab01db", "a0b1c2d3e4f5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
