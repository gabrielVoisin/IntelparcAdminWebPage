"""Multi-account platform: organizations, parkings owned by an account, management mode, super user
(app/db/sql/0005_multi_tenant.sql).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-13
"""

from pathlib import Path

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[2] / "app" / "db" / "sql" / "0005_multi_tenant.sql"


def upgrade() -> None:
    op.get_bind().connection.driver_connection.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS attach_orphans_to_internal()")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS organization_id")
    op.execute("DROP INDEX IF EXISTS devices_reference_key")
    op.execute("ALTER TABLE devices DROP COLUMN IF EXISTS qr_generated_at")
    op.execute("ALTER TABLE devices DROP COLUMN IF EXISTS reference")
    for column in ("capacity", "management_mode", "organization_id"):
        op.execute(f"ALTER TABLE sites DROP COLUMN IF EXISTS {column}")
    for column in ("is_superuser", "organization_id"):
        op.execute(f"ALTER TABLE staff_users DROP COLUMN IF EXISTS {column}")
    op.execute("DROP TABLE IF EXISTS organizations")
    op.execute("DROP TYPE IF EXISTS management_mode")
    op.execute("DROP TYPE IF EXISTS organization_kind")
