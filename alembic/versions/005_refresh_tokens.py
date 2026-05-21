"""add refresh token columns to users table

Revision ID: 005_refresh_tokens
Revises: 004_audit_log_indexes
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa

revision = "005_refresh_tokens"
down_revision = "004_audit_log_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("refresh_token_hash", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("refresh_token_expires_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("users", "refresh_token_expires_at")
    op.drop_column("users", "refresh_token_hash")
