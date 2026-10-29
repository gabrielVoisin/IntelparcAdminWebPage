"""Live view URL of the presence sensors (app/db/sql/0003_sensor_live.sql).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29
"""

from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[2] / "app" / "db" / "sql" / "0003_sensor_live.sql"


def upgrade() -> None:
    op.get_bind().connection.driver_connection.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("ALTER TABLE devices DROP COLUMN IF EXISTS live_url")
