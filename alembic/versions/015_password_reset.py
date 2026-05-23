"""add password reset columns to users table

Revision ID: 015
Revises: 014
Create Date: 2026-05-23
"""

from alembic import op
import sqlalchemy as sa

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("password_reset_jti", sa.String(32), nullable=True)
    )
    op.add_column(
        "users", sa.Column("password_reset_hash", sa.String(255), nullable=True)
    )
    op.add_column(
        "users", sa.Column("password_reset_expires_at", sa.DateTime(), nullable=True)
    )
    op.create_index(
        "ix_users_password_reset_jti",
        "users",
        ["password_reset_jti"],
        unique=True,
        postgresql_where=sa.text("password_reset_jti IS NOT NULL"),
    )


def downgrade():
    op.drop_index("ix_users_password_reset_jti")
    op.drop_column("users", "password_reset_expires_at")
    op.drop_column("users", "password_reset_hash")
    op.drop_column("users", "password_reset_jti")
