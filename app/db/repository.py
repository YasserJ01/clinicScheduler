from typing import Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Doctor, Patient, Appointment, User, UserRole, AppointmentStatus
from app.core.security import get_password_hash
from datetime import datetime, timedelta


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_username(self, username: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()

    async def create(self, username: str, password: str, role: str = "patient") -> User:
        user = User(
            username=username,
            hashed_password=get_password_hash(password),
            role=UserRole(role),
        )
        self.session.add(user)
        await self.session.flush()
        return user


class DoctorRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> Sequence[Doctor]:
        result = await self.session.execute(
            select(Doctor).where(Doctor.is_active == "true")
        )
        return result.scalars().all()

    async def get_by_id(self, doctor_id: int) -> Doctor | None:
        result = await self.session.execute(
            select(Doctor).where(Doctor.id == doctor_id)
        )
        return result.scalar_one_or_none()

    async def create(self, name: str, specialty: str) -> Doctor:
        doctor = Doctor(name=name, specialty=specialty)
        self.session.add(doctor)
        await self.session.flush()
        return doctor


class PatientRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> Sequence[Patient]:
        result = await self.session.execute(select(Patient))
        return result.scalars().all()

    async def get_by_id(self, patient_id: int) -> Patient | None:
        result = await self.session.execute(
            select(Patient).where(Patient.id == patient_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create_by_name(self, name: str, email: str) -> Patient:
        result = await self.session.execute(select(Patient).where(Patient.name == name))
        patient = result.scalar_one_or_none()
        if not patient:
            patient = Patient(name=name, email=email)
            self.session.add(patient)
            await self.session.flush()
        return patient

    async def anonymise(self, patient_id: int) -> Patient | None:
        patient = await self.get_by_id(patient_id)
        if not patient:
            return None
        patient.name = f"ANONYMIZED-{patient.id}"
        patient.email = f"anonymized-{patient.id}@redacted.local"
        patient.phone = None
        await self.session.flush()
        return patient


class AppointmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> Sequence[Appointment]:
        result = await self.session.execute(
            select(Appointment).order_by(Appointment.appointment_time)
        )
        return result.scalars().all()

    async def get_by_id(self, appointment_id: int) -> Appointment | None:
        result = await self.session.execute(
            select(Appointment).where(Appointment.id == appointment_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        doctor_id: int,
        patient_id: int,
        appointment_time: datetime,
        duration_minutes: int = 30,
    ) -> Appointment:
        naive_time = (
            appointment_time.replace(tzinfo=None)
            if appointment_time.tzinfo
            else appointment_time
        )
        appointment = Appointment(
            doctor_id=doctor_id,
            patient_id=patient_id,
            appointment_time=naive_time,
            duration_minutes=duration_minutes,
            status=AppointmentStatus.SCHEDULED,
        )
        self.session.add(appointment)
        await self.session.flush()
        return appointment

    async def get_booked_slots(
        self, doctor_id: int, date: datetime
    ) -> Sequence[Appointment]:
        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        result = await self.session.execute(
            select(Appointment)
            .where(
                Appointment.doctor_id == doctor_id,
                Appointment.appointment_time >= day_start,
                Appointment.appointment_time < day_end,
                Appointment.status != AppointmentStatus.CANCELLED,
            )
            .order_by(Appointment.appointment_time)
        )
        return result.scalars().all()

    async def check_conflict(
        self, doctor_id: int, appointment_time: datetime, duration_minutes: int = 30
    ) -> Appointment | None:
        naive_time = (
            appointment_time.replace(tzinfo=None)
            if appointment_time.tzinfo
            else appointment_time
        )
        end_time = naive_time + timedelta(minutes=duration_minutes)

        result = await self.session.execute(
            select(Appointment).where(
                Appointment.doctor_id == doctor_id,
                Appointment.appointment_time < end_time,
                Appointment.status != AppointmentStatus.CANCELLED,
            )
        )
        appointments = result.scalars().all()

        for appt in appointments:
            appt_end = appt.appointment_time + timedelta(minutes=appt.duration_minutes)
            if naive_time < appt_end:
                return appt

        return None
