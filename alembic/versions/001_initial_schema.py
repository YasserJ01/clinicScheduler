"""initial_schema

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-05-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create ENUM types
    op.execute("CREATE TYPE userrole AS ENUM ('patient', 'doctor', 'admin')")
    op.execute("CREATE TYPE appointmentstatus AS ENUM ('scheduled', 'confirmed', 'completed', 'cancelled')")

    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('username', sa.String(100), nullable=False),
        sa.Column('hashed_password', sa.String(255), nullable=False),
        sa.Column('role', sa.Enum('patient', 'doctor', 'admin', name='userrole', values_callable=lambda x: [e.value for e in x]), nullable=False, server_default='patient'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_users_username', 'users', ['username'], unique=True)

    # Create doctors table
    op.create_table(
        'doctors',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('specialty', sa.String(100), nullable=False),
        sa.Column('is_active', sa.String(10), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # Create patients table
    op.create_table(
        'patients',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('phone', sa.String(20), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_patients_email', 'patients', ['email'], unique=True)

    # Create appointments table
    op.create_table(
        'appointments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('doctor_id', sa.Integer(), sa.ForeignKey('doctors.id'), nullable=False),
        sa.Column('patient_id', sa.Integer(), sa.ForeignKey('patients.id'), nullable=False),
        sa.Column('appointment_time', sa.DateTime(), nullable=False),
        sa.Column('status', sa.Enum('scheduled', 'confirmed', 'completed', 'cancelled', name='appointmentstatus', values_callable=lambda x: [e.value for e in x]), nullable=False, server_default='scheduled'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_appointments_doctor_id', 'appointments', ['doctor_id'])
    op.create_index('ix_appointments_patient_id', 'appointments', ['patient_id'])
    op.create_index('ix_appointments_appointment_time', 'appointments', ['appointment_time'])

    # Create partial unique index for double-booking prevention
    op.execute(
        "CREATE UNIQUE INDEX uix_appointment_slot ON appointments (doctor_id, appointment_time) WHERE status != 'cancelled'"
    )

    # Create audit_log table (Phase 5)
    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('actor', sa.String(100), nullable=False),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('entity_type', sa.String(50), nullable=True),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('outcome', sa.String(20), nullable=False, server_default='success'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('audit_log')
    op.execute("DROP INDEX uix_appointment_slot")
    op.drop_index('ix_appointments_appointment_time', table_name='appointments')
    op.drop_index('ix_appointments_patient_id', table_name='appointments')
    op.drop_index('ix_appointments_doctor_id', table_name='appointments')
    op.drop_table('appointments')
    op.drop_index('ix_patients_email', table_name='patients')
    op.drop_table('patients')
    op.drop_table('doctors')
    op.drop_index('ix_users_username', table_name='users')
    op.drop_table('users')
    op.execute("DROP TYPE appointmentstatus")
    op.execute("DROP TYPE userrole")
