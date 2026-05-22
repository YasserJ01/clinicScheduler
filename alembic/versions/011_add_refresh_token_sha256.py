"""add refresh_token_sha256 column for O(1) token lookup

Revision ID: 011
Revises: 010
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("refresh_token_sha256", sa.String(64), nullable=True)
    )
    op.create_index("ix_users_refresh_token_sha256", "users", ["refresh_token_sha256"])


def downgrade():
    op.drop_index("ix_users_refresh_token_sha256", "users")
    op.drop_column("users", "refresh_token_sha256")
