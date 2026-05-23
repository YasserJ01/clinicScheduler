"""add_duration_minutes_to_appointments

Revision ID: 002_add_duration_minutes
Revises: 001_initial_schema
Create Date: 2026-05-21 00:01:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "002_add_duration_minutes"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column(
            "duration_minutes", sa.Integer(), nullable=False, server_default="30"
        ),
    )


def downgrade() -> None:
    op.drop_column("appointments", "duration_minutes")
