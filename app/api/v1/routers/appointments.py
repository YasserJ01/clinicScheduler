import socket
import logging
import math
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from datetime import datetime, timedelta
from typing import Union
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from app.db.session import get_db
from app.db.repository import AppointmentRepository, DoctorRepository, PatientRepository
from app.api.v1.dependencies import get_current_user
from app.core.audit import audit_log
from app.config import settings
from app.models import AppointmentStatus, Patient, User
from app.core.email import (
    send_booking_confirmation,
    send_cancellation_email,
    send_confirmation_email,
)

logger = logging.getLogger("clinic.appointments")

router = APIRouter(prefix="/appointments", tags=["appointments"])

NODE_ID = socket.gethostname()


class AppointmentCreate(BaseModel):
    doctor_id: int
    patient_id: Union[int, str]
    time_slot: str
    duration_minutes: int = 30

    @field_validator("time_slot")
    @classmethod
    def validate_time_slot(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            raise ValueError("time_slot must be a valid ISO 8601 datetime string")
        return v

    @field_validator("duration_minutes")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v < 5 or v > 480:
            raise ValueError("duration_minutes must be between 5 and 480 (8 hours)")
        return v


class RecurringAppointmentCreate(BaseModel):
    doctor_id: int
    patient_id: int
    start_time: str
    duration_minutes: int = 30
    recurrence: str
    occurrences: int

    @field_validator("start_time")
    @classmethod
    def validate_start_time(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            raise ValueError("start_time must be a valid ISO 8601 datetime string")
        return v

    @field_validator("duration_minutes")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v < 5 or v > 480:
            raise ValueError("duration_minutes must be between 5 and 480 (8 hours)")
        return v

    @field_validator("recurrence")
    @classmethod
    def validate_recurrence(cls, v: str) -> str:
        if v not in ("weekly", "biweekly", "monthly"):
            raise ValueError("recurrence must be 'weekly', 'biweekly', or 'monthly'")
        return v

    @field_validator("occurrences")
    @classmethod
    def validate_occurrences(cls, v: int) -> int:
        if v < 1 or v > 52:
            raise ValueError("occurrences must be between 1 and 52")
        return v


class AppointmentDetail(BaseModel):
    id: int
    doctor_id: int
    patient_id: int
    patient_name: str
    time_slot: str
    duration_minutes: int = 30
    status: str
    notes: str | None = None

    model_config = {"from_attributes": True}


class BookingResponse(BaseModel):
    success: bool
    node_id: str
    error: str | None = None
    appointment: AppointmentDetail | None = None


class NotesUpdate(BaseModel):
    notes: str


@router.get("")
async def list_appointments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    doctor_id: int | None = Query(None),
    patient_id: int | None = Query(None),
    status: str | None = Query(None),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.get("tenant_id")
    repo = AppointmentRepository(db)
    from_dt = _parse_time_slot(from_date) if from_date else None
    to_dt = _parse_time_slot(to_date) if to_date else None
    appointments, total = await repo.list_paginated(
        page=page,
        page_size=page_size,
        doctor_id=doctor_id,
        patient_id=patient_id,
        status=status,
        from_date=from_dt,
        to_date=to_dt,
        tenant_id=tenant_id,
    )
    result = []
    for appt in appointments:
        patient_repo = PatientRepository(db)
        patient = await patient_repo.get_by_id(appt.patient_id, tenant_id=tenant_id)
        result.append(
            {
                "id": appt.id,
                "doctor_id": appt.doctor_id,
                "patient_id": appt.patient_id,
                "patient_name": patient.name if patient else "Unknown",
                "time_slot": appt.appointment_time.isoformat(),
                "duration_minutes": appt.duration_minutes,
                "status": appt.status.value,
                "notes": appt.notes,
            }
        )
    pages = math.ceil(total / page_size) if total > 0 else 0
    return {
        "items": result,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.post("")
async def create_appointment(
    appt: AppointmentCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.get("tenant_id", 1)
    patient_id_str = str(appt.patient_id)

    if patient_id_str == "999" and settings.CHAOS_ENABLED:
        logger.error(
            "CHAOS: Poison pill detected — patient_id=%s on node %s",
            patient_id_str,
            NODE_ID,
        )
        raise HTTPException(status_code=503, detail="CHAOS: Simulated node failure")

    naive_time = _parse_time_slot(appt.time_slot)

    doctor_repo = DoctorRepository(db)
    doctor = await doctor_repo.get_by_id(appt.doctor_id, tenant_id=tenant_id)
    if not doctor or not doctor.is_active:
        return JSONResponse(
            status_code=400,
            content=BookingResponse(
                success=False,
                node_id=NODE_ID,
                error="Doctor not found or inactive",
            ).model_dump(),
        )

    appt_repo = AppointmentRepository(db)
    conflict = await appt_repo.check_conflict(
        appt.doctor_id, naive_time, appt.duration_minutes, tenant_id=tenant_id
    )
    if conflict:
        patient_repo = PatientRepository(db)
        holder = await patient_repo.get_by_id(conflict.patient_id, tenant_id=tenant_id)
        holder_name = holder.name if holder else "Unknown"
        conflict_resp = BookingResponse(
            success=False,
            node_id=NODE_ID,
            error=f"Slot already occupied by patient {holder_name}",
            appointment=AppointmentDetail(
                id=conflict.id,
                doctor_id=conflict.doctor_id,
                patient_id=conflict.patient_id,
                patient_name=holder_name,
                time_slot=conflict.appointment_time.isoformat(),
                duration_minutes=conflict.duration_minutes,
                status=conflict.status.value,
            ),
        )
        return JSONResponse(status_code=409, content=conflict_resp.model_dump())

    patient_repo = PatientRepository(db)
    patient = await patient_repo.get_by_id(int(patient_id_str), tenant_id=tenant_id)
    if not patient:
        return JSONResponse(
            status_code=404,
            content=BookingResponse(
                success=False,
                node_id=NODE_ID,
                error=f"Patient with id {patient_id_str} not found",
            ).model_dump(),
        )

    try:
        new_appt = await appt_repo.create(
            doctor_id=appt.doctor_id,
            patient_id=patient.id,
            appointment_time=naive_time,
            duration_minutes=appt.duration_minutes,
            tenant_id=tenant_id,
        )
    except IntegrityError:
        await db.rollback()
        conflict = await appt_repo.check_conflict(
            appt.doctor_id, naive_time, appt.duration_minutes, tenant_id=tenant_id
        )
        if conflict:
            patient_repo = PatientRepository(db)
            holder = await patient_repo.get_by_id(
                conflict.patient_id, tenant_id=tenant_id
            )
            holder_name = holder.name if holder else "Unknown"
            conflict_resp = BookingResponse(
                success=False,
                node_id=NODE_ID,
                error=f"Slot already occupied by patient {holder_name}",
                appointment=AppointmentDetail(
                    id=conflict.id,
                    doctor_id=conflict.doctor_id,
                    patient_id=conflict.patient_id,
                    patient_name=holder_name,
                    time_slot=conflict.appointment_time.isoformat(),
                    duration_minutes=conflict.duration_minutes,
                    status=conflict.status.value,
                ),
            )
            return JSONResponse(status_code=409, content=conflict_resp.model_dump())
        raise

    logger.info("Booking created: appt_id=%s on node %s", new_appt.id, NODE_ID)
    await audit_log(
        db,
        actor=current_user["user_id"],
        action="create_appointment",
        entity_type="appointment",
        entity_id=new_appt.id,
        details={
            "doctor_id": appt.doctor_id,
            "patient_id": patient.id,
            "time_slot": new_appt.appointment_time.isoformat(),
            "duration_minutes": new_appt.duration_minutes,
        },
    )
    booking = BookingResponse(
        success=True,
        node_id=NODE_ID,
        appointment=AppointmentDetail(
            id=new_appt.id,
            doctor_id=new_appt.doctor_id,
            patient_id=new_appt.patient_id,
            patient_name=patient.name,
            time_slot=new_appt.appointment_time.isoformat(),
            duration_minutes=new_appt.duration_minutes,
            status=new_appt.status.value,
        ),
    )
    appt_detail = {
        "doctor_id": new_appt.doctor_id,
        "time_slot": new_appt.appointment_time.isoformat(),
        "duration_minutes": new_appt.duration_minutes,
        "status": new_appt.status.value,
    }
    background_tasks.add_task(send_booking_confirmation, patient.email, appt_detail)
    return JSONResponse(status_code=201, content=booking.model_dump())


@router.post("/recurring")
async def create_recurring_appointment(
    req: RecurringAppointmentCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.get("tenant_id", 1)
    naive_time = _parse_time_slot(req.start_time)

    doctor_repo = DoctorRepository(db)
    doctor = await doctor_repo.get_by_id(req.doctor_id, tenant_id=tenant_id)
    if not doctor or not doctor.is_active:
        raise HTTPException(status_code=400, detail="Doctor not found or inactive")

    patient_repo = PatientRepository(db)
    patient = await patient_repo.get_by_id(req.patient_id, tenant_id=tenant_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    appt_repo = AppointmentRepository(db)
    try:
        series, created, conflicts = await appt_repo.create_recurring_series(
            doctor_id=req.doctor_id,
            patient_id=req.patient_id,
            start_time=naive_time,
            duration_minutes=req.duration_minutes,
            recurrence=req.recurrence,
            occurrences=req.occurrences,
            tenant_id=tenant_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    created_list = []
    for appt in created:
        created_list.append(
            {
                "id": appt.id,
                "doctor_id": appt.doctor_id,
                "patient_id": appt.patient_id,
                "time_slot": appt.appointment_time.isoformat(),
                "duration_minutes": appt.duration_minutes,
                "status": appt.status.value,
            }
        )

    await audit_log(
        db,
        actor=current_user["user_id"],
        action="create_recurring_appointment",
        entity_type="recurring_series",
        entity_id=series.id,
        details={
            "doctor_id": req.doctor_id,
            "patient_id": req.patient_id,
            "recurrence": req.recurrence,
            "occurrences": req.occurrences,
            "created": len(created),
            "conflicts": len(conflicts),
        },
    )

    return {
        "series_id": series.id,
        "recurrence": series.recurrence,
        "created": created_list,
        "conflicts": conflicts,
        "total_requested": req.occurrences,
        "total_created": len(created),
        "total_conflicts": len(conflicts),
    }


@router.delete("/series/{series_id}")
async def cancel_recurring_series(
    series_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = current_user.get("role", "patient")
    if role not in ("admin", "patient"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    appt_repo = AppointmentRepository(db)
    count = await appt_repo.cancel_series(series_id)

    await audit_log(
        db,
        actor=current_user["user_id"],
        action="cancel_recurring_series",
        entity_type="recurring_series",
        entity_id=series_id,
        details={"cancelled_count": count},
    )

    return {"series_id": series_id, "cancelled_count": count}


@router.get("/available")
async def get_available_slots(
    doctor_id: int,
    date: str,
    duration_minutes: int = 30,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.get("tenant_id")
    try:
        target_date = datetime.fromisoformat(date.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=422, detail="date must be a valid ISO 8601 date string"
        )

    if duration_minutes < 5 or duration_minutes > 480:
        raise HTTPException(
            status_code=422, detail="duration_minutes must be between 5 and 480"
        )

    doctor_repo = DoctorRepository(db)
    schedule = await doctor_repo.get_schedule_for_date(doctor_id, target_date)

    if schedule:
        slot_start = target_date.replace(
            hour=schedule.start_time.hour,
            minute=schedule.start_time.minute,
            second=0,
            microsecond=0,
        )
        slot_end = target_date.replace(
            hour=schedule.end_time.hour,
            minute=schedule.end_time.minute,
            second=0,
            microsecond=0,
        )
    else:
        slot_start = target_date.replace(hour=8, minute=0, second=0, microsecond=0)
        slot_end = target_date.replace(hour=17, minute=0, second=0, microsecond=0)

    repo = AppointmentRepository(db)
    booked = await repo.get_booked_slots(doctor_id, target_date, tenant_id=tenant_id)

    booked_ranges = []
    for appt in booked:
        start = appt.appointment_time
        end = start + timedelta(minutes=appt.duration_minutes)
        booked_ranges.append((start, end))

    available = []
    delta = timedelta(minutes=30)

    current = slot_start
    while current + timedelta(minutes=duration_minutes) <= slot_end:
        proposed_end = current + timedelta(minutes=duration_minutes)
        overlaps = any(
            current < booked_end and proposed_end > booked_start
            for booked_start, booked_end in booked_ranges
        )
        if not overlaps:
            available.append(current.isoformat())
        current += delta

    return {
        "doctor_id": doctor_id,
        "date": target_date.date().isoformat(),
        "duration_minutes": duration_minutes,
        "schedule_based": schedule is not None,
        "available_slots": available,
    }


class StatusUpdate(BaseModel):
    status: str


@router.patch("/{appointment_id}/status")
async def update_appointment_status(
    appointment_id: int,
    req: StatusUpdate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.get("tenant_id")
    role = current_user.get("role", "patient")
    username = current_user["user_id"]

    try:
        new_status = AppointmentStatus(req.status)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid status: {req.status}")

    appt_repo = AppointmentRepository(db)
    appt = await appt_repo.get_by_id(appointment_id, tenant_id=tenant_id)
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")

    if role == "patient":
        if new_status != AppointmentStatus.CANCELLED:
            raise HTTPException(
                status_code=403, detail="Patients can only cancel appointments"
            )
        user_result = await db.execute(
            select(User).where(User.username == username)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=403, detail="User not found")
        patient_result = await db.execute(
            select(Patient).where(Patient.user_id == user.id)
        )
        patient = patient_result.scalar_one_or_none()
        if not patient or patient.id != appt.patient_id:
            raise HTTPException(
                status_code=403, detail="Cannot cancel another patient's appointment"
            )
    elif role == "doctor":
        allowed = [
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.COMPLETED,
            AppointmentStatus.CANCELLED,
        ]
        if new_status not in allowed:
            raise HTTPException(
                status_code=403, detail="Doctors cannot set that status"
            )
    elif role != "admin":
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    try:
        updated = await appt_repo.update_status(appointment_id, new_status)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if not updated:
        raise HTTPException(status_code=404, detail="Appointment not found")

    await audit_log(
        db,
        actor=username,
        action="update_appointment_status",
        entity_type="appointment",
        entity_id=appointment_id,
        details={
            "old_status": appt.status.value,
            "new_status": new_status.value,
        },
    )

    patient_repo = PatientRepository(db)
    patient = await patient_repo.get_by_id(appt.patient_id, tenant_id=tenant_id)

    appt_detail = {
        "doctor_id": updated.doctor_id,
        "time_slot": updated.appointment_time.isoformat(),
        "duration_minutes": updated.duration_minutes,
        "status": updated.status.value,
    }
    if new_status == AppointmentStatus.CANCELLED and patient:
        background_tasks.add_task(send_cancellation_email, patient.email, appt_detail)
    elif new_status == AppointmentStatus.CONFIRMED and patient:
        background_tasks.add_task(send_confirmation_email, patient.email, appt_detail)

    return {
        "id": updated.id,
        "doctor_id": updated.doctor_id,
        "patient_id": updated.patient_id,
        "patient_name": patient.name if patient else "Unknown",
        "time_slot": updated.appointment_time.isoformat(),
        "duration_minutes": updated.duration_minutes,
        "status": updated.status.value,
    }


@router.patch("/{appointment_id}/notes")
async def update_appointment_notes(
    appointment_id: int,
    req: NotesUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.get("tenant_id")
    role = current_user.get("role", "patient")
    if role not in ("doctor", "admin"):
        raise HTTPException(
            status_code=403, detail="Only doctors and admins can update notes"
        )

    appt_repo = AppointmentRepository(db)
    appt = await appt_repo.get_by_id(appointment_id, tenant_id=tenant_id)
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    updated = await appt_repo.update_notes(appointment_id, req.notes)
    if not updated:
        raise HTTPException(status_code=404, detail="Appointment not found")

    await audit_log(
        db,
        actor=current_user["user_id"],
        action="update_appointment_notes",
        entity_type="appointment",
        entity_id=appointment_id,
        details={"notes_length": len(req.notes)},
    )

    patient_repo = PatientRepository(db)
    patient = await patient_repo.get_by_id(updated.patient_id, tenant_id=tenant_id)

    return {
        "id": updated.id,
        "doctor_id": updated.doctor_id,
        "patient_id": updated.patient_id,
        "patient_name": patient.name if patient else "Unknown",
        "time_slot": updated.appointment_time.isoformat(),
        "duration_minutes": updated.duration_minutes,
        "status": updated.status.value,
        "notes": updated.notes,
    }


@router.get("/{appointment_id}", response_model=AppointmentDetail)
async def get_appointment(
    appointment_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.get("tenant_id")
    repo = AppointmentRepository(db)
    appt = await repo.get_by_id(appointment_id, tenant_id=tenant_id)
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")

    patient_repo = PatientRepository(db)
    patient = await patient_repo.get_by_id(appt.patient_id, tenant_id=tenant_id)

    return {
        "id": appt.id,
        "doctor_id": appt.doctor_id,
        "patient_id": appt.patient_id,
        "patient_name": patient.name if patient else "Unknown",
        "time_slot": appt.appointment_time.isoformat(),
        "duration_minutes": appt.duration_minutes,
        "status": appt.status.value,
        "notes": appt.notes,
    }


def _parse_time_slot(time_slot: str) -> datetime:
    dt = datetime.fromisoformat(time_slot.replace("Z", "+00:00"))
    return dt.replace(tzinfo=None) if dt.tzinfo else dt
