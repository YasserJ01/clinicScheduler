"""enable row-level security on tenant-scoped tables

Revision ID: 018
Revises: 017
Create Date: 2026-05-23
"""

from alembic import op
import sqlalchemy as sa

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None

RLS_TABLES = [
    "tenants",
    "users",
    "doctors",
    "doctor_schedules",
    "patients",
    "appointments",
    "recurring_series",
    "audit_log",
    "webhooks",
    "webhook_deliveries",
    "api_keys",
]


def upgrade():
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
                FOR ALL
                USING (
                    tenant_id = COALESCE(
                        nullif(current_setting('app.current_tenant_id', true), ''),
                        '-1'
                    )::int
                )
        """)
        op.execute(f"""
            CREATE POLICY superadmin_bypass ON {table}
                FOR ALL
                USING (
                    current_setting('app.current_user_role', true) = 'superadmin'
                )
        """)


def downgrade():
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS superadmin_bypass ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
