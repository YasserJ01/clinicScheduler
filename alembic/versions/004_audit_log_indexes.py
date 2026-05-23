"""add indexes to audit_log table

Revision ID: 004_audit_log_indexes
Revises: 003_fix_doctor_is_active_boolean
Create Date: 2026-05-21
"""

from alembic import op

revision = "004_audit_log_indexes"
down_revision = "003_fix_doctor_is_active_boolean"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_audit_log_actor", "audit_log", ["actor"])
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade():
    op.drop_index("ix_audit_log_created_at", "audit_log")
    op.drop_index("ix_audit_log_entity", "audit_log")
    op.drop_index("ix_audit_log_actor", "audit_log")
