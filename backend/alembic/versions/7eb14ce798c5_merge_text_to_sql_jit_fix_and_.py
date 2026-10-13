"""merge text-to-sql JIT fix and generation pipeline heads

Revision ID: 7eb14ce798c5
Revises: 157c8c534a74, e8f9a0b1c2d3
Create Date: 2026-10-05 22:11:25.572178
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str | Sequence[str] | None = "7eb14ce798c5"
down_revision: str | Sequence[str] | None = ("157c8c534a74", "e8f9a0b1c2d3")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
