"""add user_id FK column to patients table

Revision ID: 012
Revises: 011
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "patients",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_patients_user_id", "patients", ["user_id"])


def downgrade():
    op.drop_index("ix_patients_user_id", "patients")
    op.drop_column("patients", "user_id")
