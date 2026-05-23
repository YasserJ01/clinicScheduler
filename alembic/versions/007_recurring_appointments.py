"""007_recurring_appointments.py

Revision ID: 007
Revises: 006
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recurring_series",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("doctor_id", sa.Integer, sa.ForeignKey("doctors.id"), nullable=False),
        sa.Column(
            "patient_id", sa.Integer, sa.ForeignKey("patients.id"), nullable=False
        ),
        sa.Column("recurrence", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime, nullable=False, server_default=sa.func.now()
        ),
    )
    op.add_column(
        "appointments",
        sa.Column(
            "series_id", sa.Integer, sa.ForeignKey("recurring_series.id"), nullable=True
        ),
    )
    op.add_column(
        "appointments", sa.Column("next_reminder_at", sa.DateTime, nullable=True)
    )
    op.add_column(
        "appointments",
        sa.Column("reminder_sent", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_appointments_series_id", "appointments", ["series_id"])


def downgrade():
    op.drop_index("ix_appointments_series_id", "appointments")
    op.drop_column("appointments", "reminder_sent")
    op.drop_column("appointments", "next_reminder_at")
    op.drop_column("appointments", "series_id")
    op.drop_table("recurring_series")
