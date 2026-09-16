"""add student subtopic goals

Revision ID: a70bf6d73a1e
Revises: f8c841992918
Create Date: 2026-09-16 18:31:16.035799
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str | Sequence[str] | None = 'a70bf6d73a1e'
down_revision: str | Sequence[str] | None = 'f8c841992918'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'student_subtopic_goals',
        sa.Column('student_id', sa.Uuid(), nullable=False),
        sa.Column('subtopic_id', sa.Uuid(), nullable=False),
        sa.Column('target_percent', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint(
            'target_percent >= 0 AND target_percent <= 100',
            name=op.f('ck_student_subtopic_goals_target_percent'),
        ),
        sa.ForeignKeyConstraint(
            ['student_id'],
            ['student_profiles.id'],
            name=op.f('fk_student_subtopic_goals_student_id_student_profiles'),
        ),
        sa.ForeignKeyConstraint(
            ['subtopic_id'],
            ['subtopics.id'],
            name=op.f('fk_student_subtopic_goals_subtopic_id_subtopics'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_student_subtopic_goals')),
        sa.UniqueConstraint(
            'student_id', 'subtopic_id', name='uq_student_subtopic_goals_student_subtopic'
        ),
    )
    op.create_index(
        op.f('ix_student_subtopic_goals_student_id'),
        'student_subtopic_goals',
        ['student_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_student_subtopic_goals_subtopic_id'),
        'student_subtopic_goals',
        ['subtopic_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_student_subtopic_goals_subtopic_id'), table_name='student_subtopic_goals')
    op.drop_index(op.f('ix_student_subtopic_goals_student_id'), table_name='student_subtopic_goals')
    op.drop_table('student_subtopic_goals')
