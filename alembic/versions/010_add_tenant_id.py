"""add tenant_id columns and tenants table

Revision ID: 010_add_tenant_id
Revises: 009
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa

revision = "010_add_tenant_id"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # Create tenants table if not exists (Alembic-based deployments skip create_all)
    if not conn.dialect.has_table(conn, "tenants"):
        op.create_table(
            "tenants",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("slug", sa.String(100), unique=True, nullable=False),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
            sa.Column(
                "created_at", sa.DateTime, nullable=False, server_default=sa.func.now()
            ),
        )
        op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)
        # Insert default tenant
        op.execute(
            "INSERT INTO tenants (name, slug) VALUES ('Default Clinic', 'default')"
        )

    # Helper: add nullable integer column with FK and index
    def add_tenant_column(table, nullable=False):
        col_name = "tenant_id"
        op.add_column(table, sa.Column(col_name, sa.Integer, nullable=True))
        op.create_foreign_key(
            f"fk_{table}_tenant",
            table,
            "tenants",
            ["tenant_id"],
            ["id"],
        )
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])

    # Tables created by migrations 001-009
    add_tenant_column("users")
    add_tenant_column("doctors")
    add_tenant_column("patients")
    add_tenant_column("appointments")
    add_tenant_column("doctor_schedules")
    add_tenant_column("recurring_series")
    add_tenant_column("webhooks")
    add_tenant_column("webhook_deliveries")

    # audit_log might not exist if only Alembic ran (it's created by create_all)
    if conn.dialect.has_table(conn, "audit_log"):
        add_tenant_column("audit_log", nullable=True)

    # Backfill existing rows to default tenant
    backfill_tables = [
        "users",
        "doctors",
        "patients",
        "appointments",
        "doctor_schedules",
        "recurring_series",
        "webhooks",
        "webhook_deliveries",
    ]
    if conn.dialect.has_table(conn, "audit_log"):
        backfill_tables.append("audit_log")

    for table in backfill_tables:
        op.execute(f"UPDATE {table} SET tenant_id = 1 WHERE tenant_id IS NULL")

    # Set NOT NULL on columns that must be non-nullable
    not_null_tables = [
        "users",
        "doctors",
        "patients",
        "appointments",
        "doctor_schedules",
        "recurring_series",
        "webhooks",
        "webhook_deliveries",
    ]
    for table in not_null_tables:
        op.alter_column(table, "tenant_id", nullable=False)


def downgrade():
    conn = op.get_bind()

    def drop_tenant_column(table):
        op.drop_index(f"ix_{table}_tenant_id", table)
        op.drop_constraint(f"fk_{table}_tenant", table, type_="foreignkey")
        op.drop_column(table, "tenant_id")

    tables = [
        "users",
        "doctors",
        "patients",
        "appointments",
        "doctor_schedules",
        "recurring_series",
        "webhooks",
        "webhook_deliveries",
    ]
    if conn.dialect.has_table(conn, "audit_log"):
        tables.append("audit_log")

    for table in reversed(tables):
        drop_tenant_column(table)

    if conn.dialect.has_table(conn, "tenants"):
        op.drop_index("ix_tenants_slug", "tenants")
        op.drop_table("tenants")
