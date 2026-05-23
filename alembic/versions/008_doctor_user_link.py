"""008_doctor_user_link.py

Revision ID: 008
Revises: 007
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "doctors",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_unique_constraint("uq_doctors_user_id", "doctors", ["user_id"])
    op.create_index("ix_doctors_user_id", "doctors", ["user_id"])


def downgrade():
    op.drop_index("ix_doctors_user_id", "doctors")
    op.drop_constraint("uq_doctors_user_id", "doctors", type_="unique")
    op.drop_column("doctors", "user_id")
