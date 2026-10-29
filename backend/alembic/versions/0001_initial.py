"""Initial schema: 25 tables, enums, triggers and views (app/db/sql/0001_schema.sql).

Revision ID: 0001
Revises:
Create Date: 2026-10-28
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[2] / "app" / "db" / "sql" / "0001_schema.sql"


def upgrade() -> None:
    # Run the script on the psycopg 3 connection itself (same transaction): with no parameters,
    # psycopg executes the multi-statement script as is, "%" and ":" included.
    op.get_bind().connection.driver_connection.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
