"""add account lockout columns to users table

Revision ID: 014
Revises: 013
Create Date: 2026-05-23
"""

from alembic import op
import sqlalchemy as sa

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column(
            "failed_login_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "users",
        sa.Column("locked_until", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_attempts")
