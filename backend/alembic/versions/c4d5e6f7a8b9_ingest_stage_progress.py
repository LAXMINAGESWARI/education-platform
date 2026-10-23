"""Store the current ingest stage on material and knowledge versions.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-10-20 10:20:00.000000

One row holds the full stage a reconnecting progress client renders. Existing
versions stay null until a worker writes them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str | Sequence[str] | None = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STAGE_SQL = (
    "stage IS NULL OR stage IN ("
    "'received', 'parsing', 'chunking', 'embedding', 'extracting_topics', 'done', 'failed'"
    ")"
)


def upgrade() -> None:
    _add("source_material_versions", "source_material_versions")
    _add("knowledge_document_versions", "knowledge_document_versions")


def downgrade() -> None:
    _drop("knowledge_document_versions", "knowledge_document_versions")
    _drop("source_material_versions", "source_material_versions")


def _add(table: str, prefix: str) -> None:
    op.add_column(table, sa.Column("stage", sa.String(length=32), nullable=True))
    op.add_column(table, sa.Column("stage_current", sa.Integer(), nullable=True))
    op.add_column(table, sa.Column("stage_total", sa.Integer(), nullable=True))
    op.add_column(table, sa.Column("stage_message", sa.Text(), nullable=True))
    op.add_column(table, sa.Column("stage_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        table,
        sa.Column("stage_seq", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(f"ck_{prefix}_stage", table, _STAGE_SQL)
    op.create_check_constraint(
        f"ck_{prefix}_stage_current",
        table,
        "stage_current IS NULL OR stage_current >= 0",
    )
    op.create_check_constraint(
        f"ck_{prefix}_stage_total",
        table,
        "stage_total IS NULL OR stage_total >= 0",
    )
    op.create_check_constraint(f"ck_{prefix}_stage_seq", table, "stage_seq >= 0")


def _drop(table: str, prefix: str) -> None:
    op.drop_constraint(f"ck_{prefix}_stage_seq", table, type_="check")
    op.drop_constraint(f"ck_{prefix}_stage_total", table, type_="check")
    op.drop_constraint(f"ck_{prefix}_stage_current", table, type_="check")
    op.drop_constraint(f"ck_{prefix}_stage", table, type_="check")
    op.drop_column(table, "stage_seq")
    op.drop_column(table, "stage_updated_at")
    op.drop_column(table, "stage_message")
    op.drop_column(table, "stage_total")
    op.drop_column(table, "stage_current")
    op.drop_column(table, "stage")
