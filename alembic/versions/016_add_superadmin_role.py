"""add superadmin role to userrole enum

Revision ID: 016
Revises: 015
Create Date: 2026-05-23
"""

from alembic import op
import sqlalchemy as sa


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE userrole ADD VALUE 'superadmin'")


def downgrade():
    op.execute("ALTER TYPE userrole RENAME TO userrole_old")
    op.execute("CREATE TYPE userrole AS ENUM ('patient', 'doctor', 'admin')")
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE userrole USING role::text::userrole"
    )
    op.execute("DROP TYPE userrole_old")
