from typing import Sequence
from sqlalchemy import select, func
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
            select(Doctor).where(Doctor.is_active.is_(True))
        )
        return result.scalars().all()

    async def list_paginated(
        self, page: int = 1, page_size: int = 20, specialty: str | None = None
    ) -> tuple[Sequence[Doctor], int]:
        page_size = min(page_size, 100)
        where_clauses = [Doctor.is_active.is_(True)]
        if specialty:
            where_clauses.append(Doctor.specialty.ilike(f"%{specialty}%"))
        count_stmt = select(func.count()).select_from(Doctor).where(*where_clauses)
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar() or 0
        stmt = (
            select(Doctor)
            .where(*where_clauses)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all(), total

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

    async def update(self, doctor_id: int, **fields) -> Doctor | None:
        doctor = await self.get_by_id(doctor_id)
        if not doctor:
            return None
        for field, value in fields.items():
            if value is not None and hasattr(doctor, field):
                setattr(doctor, field, value)
        await self.session.flush()
        return doctor


class PatientRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> Sequence[Patient]:
        result = await self.session.execute(select(Patient))
        return result.scalars().all()

    async def list_paginated(
        self, page: int = 1, page_size: int = 20, search: str | None = None
    ) -> tuple[Sequence[Patient], int]:
        page_size = min(page_size, 100)
        where_clauses = []
        if search:
            where_clauses.append(Patient.name.ilike(f"%{search}%"))
        count_stmt = select(func.count()).select_from(Patient)
        if where_clauses:
            count_stmt = count_stmt.where(*where_clauses)
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar() or 0
        stmt = select(Patient)
        if where_clauses:
            stmt = stmt.where(*where_clauses)
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await self.session.execute(stmt)
        return result.scalars().all(), total

    async def get_by_id(self, patient_id: int) -> Patient | None:
        result = await self.session.execute(
            select(Patient).where(Patient.id == patient_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create_by_email(self, name: str, email: str) -> Patient:
        result = await self.session.execute(
            select(Patient).where(Patient.email == email)
        )
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

    async def update(self, patient_id: int, **fields) -> Patient | None:
        patient = await self.get_by_id(patient_id)
        if not patient:
            return None
        for field, value in fields.items():
            if value is not None and hasattr(patient, field):
                setattr(patient, field, value)
        await self.session.flush()
        return patient


class AppointmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    VALID_TRANSITIONS = {
        "scheduled": ["confirmed", "cancelled"],
        "confirmed": ["completed", "cancelled"],
        "completed": [],
        "cancelled": [],
    }

    async def list_all(self) -> Sequence[Appointment]:
        result = await self.session.execute(
            select(Appointment).order_by(Appointment.appointment_time)
        )
        return result.scalars().all()

    async def list_paginated(
        self,
        page: int = 1,
        page_size: int = 20,
        doctor_id: int | None = None,
        patient_id: int | None = None,
        status: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> tuple[Sequence[Appointment], int]:
        page_size = min(page_size, 100)
        where_clauses = []
        if doctor_id is not None:
            where_clauses.append(Appointment.doctor_id == doctor_id)
        if patient_id is not None:
            where_clauses.append(Appointment.patient_id == patient_id)
        if status:
            where_clauses.append(Appointment.status == AppointmentStatus(status))
        if from_date:
            where_clauses.append(Appointment.appointment_time >= from_date)
        if to_date:
            where_clauses.append(Appointment.appointment_time <= to_date)
        count_stmt = select(func.count()).select_from(Appointment)
        if where_clauses:
            count_stmt = count_stmt.where(*where_clauses)
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar() or 0
        stmt = select(Appointment).order_by(Appointment.appointment_time)
        if where_clauses:
            stmt = stmt.where(*where_clauses)
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await self.session.execute(stmt)
        return result.scalars().all(), total

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
        lower_bound = naive_time - timedelta(minutes=480)

        result = await self.session.execute(
            select(Appointment).where(
                Appointment.doctor_id == doctor_id,
                Appointment.appointment_time >= lower_bound,
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
