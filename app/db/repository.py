from typing import Sequence
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import (
    Doctor,
    Patient,
    Appointment,
    User,
    UserRole,
    AppointmentStatus,
    DoctorSchedule,
    RecurringSeries,
)
from app.core.security import get_password_hash
from datetime import datetime, timedelta


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_username(
        self, username: str, tenant_id: int | None = None
    ) -> User | None:
        where_clauses = [User.username == username]
        if tenant_id is not None:
            where_clauses.append(User.tenant_id == tenant_id)
        result = await self.session.execute(select(User).where(*where_clauses))
        return result.scalar_one_or_none()

    async def create(
        self, username: str, password: str, role: str = "patient", tenant_id: int = 1
    ) -> User:
        user = User(
            username=username,
            hashed_password=get_password_hash(password),
            role=UserRole(role),
            tenant_id=tenant_id,
        )
        self.session.add(user)
        await self.session.flush()
        return user


class DoctorRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self, tenant_id: int | None = None) -> Sequence[Doctor]:
        where_clauses = [Doctor.is_active.is_(True)]
        if tenant_id is not None:
            where_clauses.append(Doctor.tenant_id == tenant_id)
        result = await self.session.execute(select(Doctor).where(*where_clauses))
        return result.scalars().all()

    async def list_paginated(
        self,
        page: int = 1,
        page_size: int = 20,
        specialty: str | None = None,
        tenant_id: int | None = None,
    ) -> tuple[Sequence[Doctor], int]:
        page_size = min(page_size, 100)
        where_clauses = [Doctor.is_active.is_(True)]
        if tenant_id is not None:
            where_clauses.append(Doctor.tenant_id == tenant_id)
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

    async def get_by_id(
        self, doctor_id: int, tenant_id: int | None = None
    ) -> Doctor | None:
        where_clauses = [Doctor.id == doctor_id]
        if tenant_id is not None:
            where_clauses.append(Doctor.tenant_id == tenant_id)
        result = await self.session.execute(select(Doctor).where(*where_clauses))
        return result.scalar_one_or_none()

    async def create(self, name: str, specialty: str, tenant_id: int = 1) -> Doctor:
        doctor = Doctor(name=name, specialty=specialty, tenant_id=tenant_id)
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

    async def get_schedule(self, doctor_id: int) -> Sequence[DoctorSchedule]:
        result = await self.session.execute(
            select(DoctorSchedule)
            .where(
                DoctorSchedule.doctor_id == doctor_id,
                DoctorSchedule.is_active.is_(True),
            )
            .order_by(DoctorSchedule.day_of_week)
        )
        return result.scalars().all()

    async def set_schedule(
        self, doctor_id: int, schedules: list[dict], tenant_id: int = 1
    ) -> Sequence[DoctorSchedule]:
        result = await self.session.execute(
            select(DoctorSchedule).where(DoctorSchedule.doctor_id == doctor_id)
        )
        existing = result.scalars().all()
        for s in existing:
            await self.session.delete(s)
        await self.session.flush()
        new_schedules = []
        for s in schedules:
            ns = DoctorSchedule(
                doctor_id=doctor_id,
                tenant_id=tenant_id,
                day_of_week=s["day_of_week"],
                start_time=s["start_time"],
                end_time=s["end_time"],
                is_active=s.get("is_active", True),
            )
            self.session.add(ns)
            new_schedules.append(ns)
        await self.session.flush()
        return new_schedules

    async def update_schedule_day(
        self, doctor_id: int, day_of_week: int, **fields
    ) -> DoctorSchedule | None:
        result = await self.session.execute(
            select(DoctorSchedule).where(
                DoctorSchedule.doctor_id == doctor_id,
                DoctorSchedule.day_of_week == day_of_week,
            )
        )
        schedule = result.scalar_one_or_none()
        if not schedule:
            return None
        for field, value in fields.items():
            if value is not None and hasattr(schedule, field):
                setattr(schedule, field, value)
        await self.session.flush()
        return schedule

    async def delete_schedule_day(self, doctor_id: int, day_of_week: int) -> bool:
        result = await self.session.execute(
            select(DoctorSchedule).where(
                DoctorSchedule.doctor_id == doctor_id,
                DoctorSchedule.day_of_week == day_of_week,
            )
        )
        schedule = result.scalar_one_or_none()
        if not schedule:
            return False
        await self.session.delete(schedule)
        await self.session.flush()
        return True

    async def get_schedule_for_date(
        self, doctor_id: int, date: datetime
    ) -> DoctorSchedule | None:
        day_of_week = date.weekday()
        result = await self.session.execute(
            select(DoctorSchedule).where(
                DoctorSchedule.doctor_id == doctor_id,
                DoctorSchedule.day_of_week == day_of_week,
                DoctorSchedule.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()


class PatientRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self, tenant_id: int | None = None) -> Sequence[Patient]:
        where_clauses = []
        if tenant_id is not None:
            where_clauses.append(Patient.tenant_id == tenant_id)
        result = await self.session.execute(select(Patient).where(*where_clauses))
        return result.scalars().all()

    async def list_paginated(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        tenant_id: int | None = None,
    ) -> tuple[Sequence[Patient], int]:
        page_size = min(page_size, 100)
        where_clauses = []
        if tenant_id is not None:
            where_clauses.append(Patient.tenant_id == tenant_id)
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

    async def get_by_id(
        self, patient_id: int, tenant_id: int | None = None
    ) -> Patient | None:
        where_clauses = [Patient.id == patient_id]
        if tenant_id is not None:
            where_clauses.append(Patient.tenant_id == tenant_id)
        result = await self.session.execute(select(Patient).where(*where_clauses))
        return result.scalar_one_or_none()

    async def get_or_create_by_email(
        self, name: str, email: str, tenant_id: int = 1, user_id: int | None = None
    ) -> Patient:
        result = await self.session.execute(
            select(Patient).where(
                Patient.email == email, Patient.tenant_id == tenant_id
            )
        )
        patient = result.scalar_one_or_none()
        if not patient:
            patient = Patient(
                name=name, email=email, tenant_id=tenant_id, user_id=user_id
            )
            self.session.add(patient)
            await self.session.flush()
        else:
            if user_id is not None and patient.user_id is None:
                patient.user_id = user_id
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

    async def get_patients_for_doctor(
        self, doctor_id: int, tenant_id: int | None = None
    ) -> Sequence[Patient]:
        where_clauses = [Appointment.doctor_id == doctor_id]
        if tenant_id is not None:
            where_clauses.append(Appointment.tenant_id == tenant_id)
        result = await self.session.execute(
            select(Patient)
            .join(Appointment, Patient.id == Appointment.patient_id)
            .where(*where_clauses)
            .distinct()
            .order_by(Patient.name)
        )
        return result.scalars().all()


class AppointmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    VALID_TRANSITIONS = {
        "scheduled": ["confirmed", "cancelled"],
        "confirmed": ["completed", "cancelled"],
        "completed": [],
        "cancelled": [],
    }

    async def list_all(self, tenant_id: int | None = None) -> Sequence[Appointment]:
        where_clauses = []
        if tenant_id is not None:
            where_clauses.append(Appointment.tenant_id == tenant_id)
        result = await self.session.execute(
            select(Appointment)
            .where(*where_clauses)
            .order_by(Appointment.appointment_time)
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
        tenant_id: int | None = None,
    ) -> tuple[Sequence[Appointment], int]:
        page_size = min(page_size, 100)
        where_clauses = []
        if tenant_id is not None:
            where_clauses.append(Appointment.tenant_id == tenant_id)
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

    async def get_by_id(
        self, appointment_id: int, tenant_id: int | None = None
    ) -> Appointment | None:
        where_clauses = [Appointment.id == appointment_id]
        if tenant_id is not None:
            where_clauses.append(Appointment.tenant_id == tenant_id)
        result = await self.session.execute(select(Appointment).where(*where_clauses))
        return result.scalar_one_or_none()

    async def create(
        self,
        doctor_id: int,
        patient_id: int,
        appointment_time: datetime,
        duration_minutes: int = 30,
        tenant_id: int = 1,
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
            tenant_id=tenant_id,
        )
        self.session.add(appointment)
        await self.session.flush()
        return appointment

    async def get_booked_slots(
        self, doctor_id: int, date: datetime, tenant_id: int | None = None
    ) -> Sequence[Appointment]:
        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        where_clauses = [
            Appointment.doctor_id == doctor_id,
            Appointment.appointment_time >= day_start,
            Appointment.appointment_time < day_end,
            Appointment.status != AppointmentStatus.CANCELLED,
        ]
        if tenant_id is not None:
            where_clauses.append(Appointment.tenant_id == tenant_id)
        result = await self.session.execute(
            select(Appointment)
            .where(*where_clauses)
            .order_by(Appointment.appointment_time)
        )
        return result.scalars().all()

    async def check_conflict(
        self,
        doctor_id: int,
        appointment_time: datetime,
        duration_minutes: int = 30,
        tenant_id: int | None = None,
    ) -> Appointment | None:
        naive_time = (
            appointment_time.replace(tzinfo=None)
            if appointment_time.tzinfo
            else appointment_time
        )
        end_time = naive_time + timedelta(minutes=duration_minutes)
        lower_bound = naive_time - timedelta(minutes=480)

        where_clauses = [
            Appointment.doctor_id == doctor_id,
            Appointment.appointment_time >= lower_bound,
            Appointment.appointment_time < end_time,
            Appointment.status != AppointmentStatus.CANCELLED,
        ]
        if tenant_id is not None:
            where_clauses.append(Appointment.tenant_id == tenant_id)

        result = await self.session.execute(select(Appointment).where(*where_clauses))
        appointments = result.scalars().all()

        for appt in appointments:
            appt_end = appt.appointment_time + timedelta(minutes=appt.duration_minutes)
            if naive_time < appt_end:
                return appt

        return None

    async def update_status(
        self, appointment_id: int, new_status: AppointmentStatus
    ) -> Appointment | None:
        appt = await self.get_by_id(appointment_id)
        if not appt:
            return None
        current_status = appt.status.value
        allowed = self.VALID_TRANSITIONS.get(current_status, [])
        if new_status.value not in allowed:
            raise ValueError(
                f"Cannot transition from '{current_status}' to '{new_status.value}'"
            )
        appt.status = new_status
        await self.session.flush()
        return appt

    async def update_notes(self, appointment_id: int, notes: str) -> Appointment | None:
        appt = await self.get_by_id(appointment_id)
        if not appt:
            return None
        appt.notes = notes
        await self.session.flush()
        return appt

    async def create_recurring_series(
        self,
        doctor_id: int,
        patient_id: int,
        start_time: datetime,
        duration_minutes: int,
        recurrence: str,
        occurrences: int,
        tenant_id: int = 1,
    ) -> tuple[RecurringSeries, list[Appointment], list[dict]]:
        series = RecurringSeries(
            doctor_id=doctor_id,
            patient_id=patient_id,
            recurrence=recurrence,
            tenant_id=tenant_id,
        )
        self.session.add(series)
        await self.session.flush()

        created = []
        conflicts = []
        current = start_time.replace(tzinfo=None)

        for i in range(occurrences):
            conflict = await self.check_conflict(
                doctor_id, current, duration_minutes, tenant_id=tenant_id
            )
            if conflict:
                conflicts.append(
                    {
                        "time_slot": current.isoformat(),
                        "reason": "Slot already occupied",
                    }
                )
            else:
                appt = Appointment(
                    doctor_id=doctor_id,
                    patient_id=patient_id,
                    appointment_time=current,
                    duration_minutes=duration_minutes,
                    status=AppointmentStatus.SCHEDULED,
                    series_id=series.id,
                    tenant_id=tenant_id,
                )
                self.session.add(appt)
                created.append(appt)

            if recurrence == "weekly":
                current += timedelta(weeks=1)
            elif recurrence == "biweekly":
                current += timedelta(weeks=2)
            elif recurrence == "monthly":
                month = current.month + 1
                year = current.year
                if month > 12:
                    month = 1
                    year += 1
                day = min(current.day, 28)
                current = current.replace(year=year, month=month, day=day)
            else:
                raise ValueError(f"Invalid recurrence: {recurrence}")

        await self.session.flush()
        return series, created, conflicts

    async def cancel_series(self, series_id: int) -> int:
        result = await self.session.execute(
            select(Appointment).where(
                Appointment.series_id == series_id,
                Appointment.status.in_(["scheduled", "confirmed"]),
            )
        )
        appointments = result.scalars().all()
        count = 0
        for appt in appointments:
            appt.status = AppointmentStatus.CANCELLED
            count += 1
        await self.session.flush()
        return count

    async def get_due_reminders(
        self, tenant_id: int | None = None
    ) -> Sequence[Appointment]:
        now = datetime.utcnow()
        where_clauses = [
            Appointment.appointment_time <= now + timedelta(hours=24),
            Appointment.appointment_time > now,
            Appointment.status.in_(["scheduled", "confirmed"]),
            Appointment.reminder_sent.is_(False),
            Appointment.next_reminder_at <= now,
        ]
        if tenant_id is not None:
            where_clauses.append(Appointment.tenant_id == tenant_id)
        result = await self.session.execute(select(Appointment).where(*where_clauses))
        return result.scalars().all()

    async def mark_reminder_sent(self, appointment_id: int) -> None:
        result = await self.session.execute(
            select(Appointment).where(Appointment.id == appointment_id)
        )
        appt = result.scalar_one_or_none()
        if appt:
            appt.reminder_sent = True
            appt.next_reminder_at = None
            await self.session.flush()

    async def get_today_appointments(
        self, doctor_id: int, tenant_id: int | None = None
    ) -> Sequence[Appointment]:
        now = datetime.utcnow()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        where_clauses = [
            Appointment.doctor_id == doctor_id,
            Appointment.appointment_time >= day_start,
            Appointment.appointment_time < day_end,
        ]
        if tenant_id is not None:
            where_clauses.append(Appointment.tenant_id == tenant_id)
        result = await self.session.execute(
            select(Appointment)
            .where(*where_clauses)
            .order_by(Appointment.appointment_time)
        )
        return result.scalars().all()

    async def get_upcoming_appointments(
        self, doctor_id: int, days: int = 7, tenant_id: int | None = None
    ) -> Sequence[Appointment]:
        now = datetime.utcnow()
        end = now + timedelta(days=days)
        where_clauses = [
            Appointment.doctor_id == doctor_id,
            Appointment.appointment_time >= now,
            Appointment.appointment_time <= end,
            Appointment.status.in_(["scheduled", "confirmed"]),
        ]
        if tenant_id is not None:
            where_clauses.append(Appointment.tenant_id == tenant_id)
        result = await self.session.execute(
            select(Appointment)
            .where(*where_clauses)
            .order_by(Appointment.appointment_time)
        )
        return result.scalars().all()
