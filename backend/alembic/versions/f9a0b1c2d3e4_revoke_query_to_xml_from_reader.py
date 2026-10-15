"""Revoke SQL-from-string XML helpers from text_to_sql_reader / PUBLIC.

`query_to_xml` (and siblings that take a SQL text argument) execute that string
inside Postgres. validate_sql's AST walker cannot see `set_config` buried in the
string body, so an otherwise-blocked GUC rewrite reaches the DB. PUBLIC has
EXECUTE on these by default; revoking from PUBLIC (and the reader role) is the
DB-level backstop matching the table-grant philosophy of migration
`c9d0e1f2a3b4`. Superusers still bypass privilege checks; the app `education`
role does not use these helpers.

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-10-12 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str | Sequence[str] | None = "f9a0b1c2d3e4"
down_revision: str | Sequence[str] | None = "e8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_READER_ROLE = "text_to_sql_reader"

# Identity arguments from pg_catalog on Postgres 16 (stable across 14+).
_QUERY_XML_FUNCTIONS: tuple[str, ...] = (
    "pg_catalog.query_to_xml(text, boolean, boolean, text)",
    "pg_catalog.query_to_xmlschema(text, boolean, boolean, text)",
    "pg_catalog.query_to_xml_and_xmlschema(text, boolean, boolean, text)",
    "pg_catalog.cursor_to_xml(refcursor, integer, boolean, boolean, text)",
    "pg_catalog.cursor_to_xmlschema(refcursor, boolean, boolean, text)",
)


def upgrade() -> None:
    for signature in _QUERY_XML_FUNCTIONS:
        # PUBLIC grant is the default; REVOKE FROM the role alone would leave
        # EXECUTE intact via PUBLIC.
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM {_READER_ROLE}")


def downgrade() -> None:
    for signature in _QUERY_XML_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO PUBLIC")
