"""Driver app accounts, Bluetooth identification, evidence photos (app/db/sql/0002_driver_app_bluetooth.sql).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28
"""

from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[2] / "app" / "db" / "sql" / "0002_driver_app_bluetooth.sql"


def upgrade() -> None:
    # A new enum value cannot be used in the transaction that adds it: add it in an autonomous block first.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE alarm_reason ADD VALUE IF NOT EXISTS 'reserved_bay_misuse'")
    op.get_bind().connection.driver_connection.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("""
        DROP VIEW IF EXISTS v_customer_rights;
        DROP TABLE IF EXISTS alarm_evidence, ble_identifications, customer_phone_codes, customer_refresh_tokens;
        ALTER TABLE payments DROP COLUMN IF EXISTS provider;
        ALTER TABLE parking_sessions DROP COLUMN IF EXISTS customer_id, DROP COLUMN IF EXISTS identified_by,
                                     DROP COLUMN IF EXISTS identified_at;
        DROP INDEX IF EXISTS customers_app_email_uq;
        ALTER TABLE customers DROP COLUMN IF EXISTS password_hash, DROP COLUMN IF EXISTS phone_verified_at,
                              DROP COLUMN IF EXISTS last_login_at;
        DROP SEQUENCE IF EXISTS customer_reference_seq;
        DROP TYPE IF EXISTS identification_status;
        DROP TYPE IF EXISTS identification_method;
    """)
    # PostgreSQL cannot drop an enum value: 'reserved_bay_misuse' stays in alarm_reason (unused).
