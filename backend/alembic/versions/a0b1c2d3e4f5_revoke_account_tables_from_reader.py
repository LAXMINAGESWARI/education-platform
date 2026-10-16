"""Revoke account tables from text_to_sql_reader and merge heads.

`users`, `user_roles`, and `refresh_sessions` were readable by the text-to-SQL
reader. Column grants hid `password_hash` and `token_hash`, and row-level security
hid other institutions, but a teacher still received every login in their own
school. Application checks now reject these tables; this revokes the grants so a
query that bypasses those checks still cannot read them.

This revision also merges the two current heads (`7eb14ce798c5` and
`f9a0b1c2d3e4`) so `alembic upgrade head` works again.

Revision ID: a0b1c2d3e4f5
Revises: 7eb14ce798c5, f9a0b1c2d3e4
Create Date: 2026-10-16 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str | Sequence[str] | None = "a0b1c2d3e4f5"
down_revision: str | Sequence[str] | None = ("7eb14ce798c5", "f9a0b1c2d3e4")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_READER_ROLE = "text_to_sql_reader"
_ACCOUNT_TABLES = ("users", "user_roles", "refresh_sessions")

# Previous column grants, restored only on downgrade. Matches c9d0e1f2a3b4.
_USERS_COLUMNS = "id, institution_id, email, full_name, status, created_at, updated_at"
_REFRESH_SESSIONS_COLUMNS = "id, user_id, expires_at, revoked_at, created_at, updated_at"


def upgrade() -> None:
    for table in _ACCOUNT_TABLES:
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM {_READER_ROLE}")


def downgrade() -> None:
    op.execute(f"GRANT SELECT ({_USERS_COLUMNS}) ON users TO {_READER_ROLE}")
    op.execute(f"GRANT SELECT ON user_roles TO {_READER_ROLE}")
    op.execute(f"GRANT SELECT ({_REFRESH_SESSIONS_COLUMNS}) ON refresh_sessions TO {_READER_ROLE}")
