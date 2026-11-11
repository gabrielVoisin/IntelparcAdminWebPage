"""QR code reference, street, GPS position and rental status of the bays (app/db/sql/0004_bay_qr_rental.sql).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-11
"""

from pathlib import Path

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[2] / "app" / "db" / "sql" / "0004_bay_qr_rental.sql"


def upgrade() -> None:
    op.get_bind().connection.driver_connection.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS bays_reference_key")
    for column in ("qr_generated_at", "longitude", "latitude", "street", "reference", "rental_status"):
        op.execute(f"ALTER TABLE bays DROP COLUMN IF EXISTS {column}")
    op.execute("DROP TYPE IF EXISTS bay_rental_status")
