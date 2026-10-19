"""Store original chunk-group labels on outline nodes.

Revision ID: e7f8a9b0c1d2
Revises: b7c8d9e0f1a2
Create Date: 2026-10-19 10:30:00.000000

Display titles can be cleaned without moving PDF chunks. Existing subtopic
names are not rewritten; this column starts empty and legacy rows keep
matching chunks by title.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str | Sequence[str] | None = "e7f8a9b0c1d2"
down_revision: str | Sequence[str] | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "content_generation_outline_nodes",
        sa.Column(
            "source_headings",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "generation_outline_revision_nodes",
        sa.Column(
            "source_headings",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("generation_outline_revision_nodes", "source_headings")
    op.drop_column("content_generation_outline_nodes", "source_headings")
