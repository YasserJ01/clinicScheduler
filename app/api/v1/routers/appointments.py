import socket
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from datetime import datetime, timedelta
from typing import Union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from app.db.session import get_db
from app.db.repository import AppointmentRepository, DoctorRepository, PatientRepository
from app.api.v1.dependencies import get_current_user
from app.core.audit import audit_log
from app.config import settings

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


class AppointmentDetail(BaseModel):
    id: int
    doctor_id: int
    patient_id: int
    patient_name: str
    time_slot: str
    duration_minutes: int = 30
    status: str

    model_config = {"from_attributes": True}


class BookingResponse(BaseModel):
    success: bool
    node_id: str
    error: str | None = None
    appointment: AppointmentDetail | None = None


@router.get("", response_model=list[AppointmentDetail])
async def list_appointments(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = AppointmentRepository(db)
    appointments = await repo.list_all()
    result = []
    for appt in appointments:
        patient_repo = PatientRepository(db)
        patient = await patient_repo.get_by_id(appt.patient_id)
        result.append(
            {
                "id": appt.id,
                "doctor_id": appt.doctor_id,
                "patient_id": appt.patient_id,
                "patient_name": patient.name if patient else "Unknown",
                "time_slot": appt.appointment_time.isoformat(),
                "duration_minutes": appt.duration_minutes,
                "status": appt.status.value,
            }
        )
    return result


@router.post("")
async def create_appointment(
    appt: AppointmentCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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
    doctor = await doctor_repo.get_by_id(appt.doctor_id)
    if not doctor:
        return JSONResponse(
            status_code=400,
            content=BookingResponse(
                success=False,
                node_id=NODE_ID,
                error="Doctor not found",
            ).model_dump(),
        )

    appt_repo = AppointmentRepository(db)
    conflict = await appt_repo.check_conflict(
        appt.doctor_id, naive_time, appt.duration_minutes
    )
    if conflict:
        patient_repo = PatientRepository(db)
        holder = await patient_repo.get_by_id(conflict.patient_id)
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
    patient = await patient_repo.get_by_id(int(patient_id_str))
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
        )
    except IntegrityError:
        await db.rollback()
        conflict = await appt_repo.check_conflict(
            appt.doctor_id, naive_time, appt.duration_minutes
        )
        if conflict:
            patient_repo = PatientRepository(db)
            holder = await patient_repo.get_by_id(conflict.patient_id)
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
    return JSONResponse(status_code=201, content=booking.model_dump())


@router.get("/available")
async def get_available_slots(
    doctor_id: int,
    date: str,
    duration_minutes: int = 30,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get available time slots for a doctor on a given date.

    Returns 30-minute slots from 08:00 to 17:00 that are not booked.
    """
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

    repo = AppointmentRepository(db)
    booked = await repo.get_booked_slots(doctor_id, target_date)

    booked_ranges = []
    for appt in booked:
        start = appt.appointment_time
        end = start + timedelta(minutes=appt.duration_minutes)
        booked_ranges.append((start, end))

    available = []
    slot_start = target_date.replace(hour=8, minute=0, second=0, microsecond=0)
    slot_end = target_date.replace(hour=17, minute=0, second=0, microsecond=0)
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
        "available_slots": available,
    }


@router.get("/{appointment_id}", response_model=AppointmentDetail)
async def get_appointment(
    appointment_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = AppointmentRepository(db)
    appt = await repo.get_by_id(appointment_id)
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")

    patient_repo = PatientRepository(db)
    patient = await patient_repo.get_by_id(appt.patient_id)

    return {
        "id": appt.id,
        "doctor_id": appt.doctor_id,
        "patient_id": appt.patient_id,
        "patient_name": patient.name if patient else "Unknown",
        "time_slot": appt.appointment_time.isoformat(),
        "duration_minutes": appt.duration_minutes,
        "status": appt.status.value,
    }


def _parse_time_slot(time_slot: str) -> datetime:
    dt = datetime.fromisoformat(time_slot.replace("Z", "+00:00"))
    return dt.replace(tzinfo=None) if dt.tzinfo else dt
