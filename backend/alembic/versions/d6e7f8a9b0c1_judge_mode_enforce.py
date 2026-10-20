"""Allow enforce as the Jev judge mode.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-10-19 21:10:00.000000

Replaces augment/replace with enforce. Existing shadow and off rows stay valid.
"""

from collections.abc import Sequence

from alembic import op

revision: str | Sequence[str] | None = "d6e7f8a9b0c1"
down_revision: str | Sequence[str] | None = "c5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_judge_evaluations_mode", "judge_evaluations", type_="check")
    op.create_check_constraint(
        "ck_judge_evaluations_mode",
        "judge_evaluations",
        "mode IN ('off', 'shadow', 'enforce')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_judge_evaluations_mode", "judge_evaluations", type_="check")
    op.create_check_constraint(
        "ck_judge_evaluations_mode",
        "judge_evaluations",
        "mode IN ('off', 'shadow', 'augment', 'replace')",
    )
