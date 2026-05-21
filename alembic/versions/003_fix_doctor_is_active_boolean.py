"""fix doctor is_active to boolean column

Revision ID: 003_fix_doctor_is_active_boolean
Revises: 002_add_duration_minutes
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa

revision = "003_fix_doctor_is_active_boolean"
down_revision = "002_add_duration_minutes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE doctors ADD COLUMN is_active_bool BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute("UPDATE doctors SET is_active_bool = (is_active = 'true')")
    op.execute("ALTER TABLE doctors DROP COLUMN is_active")
    op.execute("ALTER TABLE doctors RENAME COLUMN is_active_bool TO is_active")


def downgrade():
    op.execute("ALTER TABLE doctors ADD COLUMN is_active_str VARCHAR(10) NOT NULL DEFAULT 'true'")
    op.execute("UPDATE doctors SET is_active_str = CASE WHEN is_active THEN 'true' ELSE 'false' END")
    op.execute("ALTER TABLE doctors DROP COLUMN is_active")
    op.execute("ALTER TABLE doctors RENAME COLUMN is_active_str TO is_active")
