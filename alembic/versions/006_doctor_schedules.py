"""006_doctor_schedules.py

Revision ID: 006
Revises: 005
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "doctor_schedules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("doctor_id", sa.Integer, sa.ForeignKey("doctors.id"), nullable=False),
        sa.Column("day_of_week", sa.Integer, nullable=False),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime, nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("doctor_id", "day_of_week"),
    )
    op.create_index("ix_doctor_schedules_doctor_id", "doctor_schedules", ["doctor_id"])


def downgrade():
    op.drop_index("ix_doctor_schedules_doctor_id", "doctor_schedules")
    op.drop_table("doctor_schedules")
