"""009_webhooks.py

Revision ID: 009
Revises: 008
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "webhooks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("secret", sa.String(255), nullable=False),
        sa.Column("events", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime, nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "webhook_id", sa.Integer, sa.ForeignKey("webhooks.id"), nullable=False
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("response_status", sa.Integer, nullable=True),
        sa.Column("response_body", sa.Text, nullable=True),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
        sa.Column("success", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime, nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_webhook_deliveries_webhook_id", "webhook_deliveries", ["webhook_id"]
    )


def downgrade():
    op.drop_index("ix_webhook_deliveries_webhook_id", "webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")
