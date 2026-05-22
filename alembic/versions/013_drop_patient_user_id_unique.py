"""drop unique constraint on patient user_id

Revision ID: 013
Revises: 012
Create Date: 2026-05-22
"""

from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("patients_user_id_key", "patients", type_="unique")


def downgrade():
    op.create_unique_constraint("patients_user_id_key", "patients", ["user_id"])
