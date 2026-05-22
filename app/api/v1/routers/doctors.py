import math
from datetime import datetime, time as dt_time
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.db.repository import DoctorRepository, AppointmentRepository, PatientRepository
from app.api.v1.dependencies import get_current_user
from app.core.audit import audit_log

router = APIRouter(prefix="/doctors", tags=["doctors"])


class DoctorCreate(BaseModel):
    name: str
    specialty: str


class DoctorUpdate(BaseModel):
    name: str | None = None
    specialty: str | None = None
    is_active: bool | None = None


class DoctorResponse(BaseModel):
    id: int
    name: str
    specialty: str
    is_active: bool

    model_config = {"from_attributes": True}


class DoctorProfileResponse(BaseModel):
    id: int
    name: str
    specialty: str
    is_active: bool
    appointments_today: int = 0
    upcoming_appointments: int = 0

    model_config = {"from_attributes": True}


class ScheduleEntry(BaseModel):
    day_of_week: int
    start_time: str
    end_time: str
    is_active: bool = True


class ScheduleDayUpdate(BaseModel):
    start_time: str | None = None
    end_time: str | None = None
    is_active: bool | None = None


def _parse_time(t: str) -> dt_time:
    parts = t.split(":")
    return dt_time(hour=int(parts[0]), minute=int(parts[1]))


@router.get("")
async def list_doctors(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    specialty: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = DoctorRepository(db)
    doctors, total = await repo.list_paginated(
        page=page, page_size=page_size, specialty=specialty
    )
    items = [
        {"id": d.id, "name": d.name, "specialty": d.specialty, "is_active": d.is_active}
        for d in doctors
    ]
    pages = math.ceil(total / page_size) if total > 0 else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.post("", response_model=DoctorResponse, status_code=201)
async def create_doctor(
    doctor: DoctorCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    repo = DoctorRepository(db)
    new_doctor = await repo.create(name=doctor.name, specialty=doctor.specialty)
    return {
        "id": new_doctor.id,
        "name": new_doctor.name,
        "specialty": new_doctor.specialty,
        "is_active": new_doctor.is_active,
    }


@router.get("/{doctor_id}", response_model=DoctorProfileResponse)
async def get_doctor(
    doctor_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = DoctorRepository(db)
    doctor = await repo.get_by_id(doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return {
        "id": doctor.id,
        "name": doctor.name,
        "specialty": doctor.specialty,
        "is_active": doctor.is_active,
        "appointments_today": 0,
        "upcoming_appointments": 0,
    }


@router.patch("/{doctor_id}", response_model=DoctorResponse)
async def update_doctor(
    doctor_id: int,
    req: DoctorUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    repo = DoctorRepository(db)
    updated = await repo.update(
        doctor_id, name=req.name, specialty=req.specialty, is_active=req.is_active
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Doctor not found")
    await audit_log(
        db,
        actor=current_user["user_id"],
        action="update_doctor",
        entity_type="doctor",
        entity_id=doctor_id,
        details={
            "name": req.name,
            "specialty": req.specialty,
            "is_active": req.is_active,
        },
    )
    return {
        "id": updated.id,
        "name": updated.name,
        "specialty": updated.specialty,
        "is_active": updated.is_active,
    }


@router.get("/{doctor_id}/schedule")
async def get_doctor_schedule(
    doctor_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = DoctorRepository(db)
    doctor = await repo.get_by_id(doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    schedules = await repo.get_schedule(doctor_id)
    return [
        {
            "id": s.id,
            "doctor_id": s.doctor_id,
            "day_of_week": s.day_of_week,
            "start_time": s.start_time.isoformat(),
            "end_time": s.end_time.isoformat(),
            "is_active": s.is_active,
        }
        for s in schedules
    ]


@router.put("/{doctor_id}/schedule")
async def set_doctor_schedule(
    doctor_id: int,
    schedules: list[ScheduleEntry],
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = current_user.get("role", "patient")
    if role not in ("admin", "doctor"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    repo = DoctorRepository(db)
    doctor = await repo.get_by_id(doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    if role == "doctor":
        raise HTTPException(status_code=403, detail="Only admins can set schedules")

    schedule_data = [
        {
            "day_of_week": s.day_of_week,
            "start_time": _parse_time(s.start_time),
            "end_time": _parse_time(s.end_time),
            "is_active": s.is_active,
        }
        for s in schedules
    ]

    result = await repo.set_schedule(doctor_id, schedule_data)
    await audit_log(
        db,
        actor=current_user["user_id"],
        action="set_doctor_schedule",
        entity_type="doctor_schedule",
        entity_id=doctor_id,
        details={"days_count": len(result)},
    )

    return [
        {
            "id": s.id,
            "doctor_id": s.doctor_id,
            "day_of_week": s.day_of_week,
            "start_time": s.start_time.isoformat(),
            "end_time": s.end_time.isoformat(),
            "is_active": s.is_active,
        }
        for s in result
    ]


@router.patch("/{doctor_id}/schedule/{day_of_week}")
async def update_schedule_day(
    doctor_id: int,
    day_of_week: int,
    req: ScheduleDayUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    if day_of_week < 0 or day_of_week > 6:
        raise HTTPException(
            status_code=422, detail="day_of_week must be 0-6 (Monday-Sunday)"
        )

    repo = DoctorRepository(db)
    doctor = await repo.get_by_id(doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    fields = {}
    if req.start_time is not None:
        fields["start_time"] = _parse_time(req.start_time)
    if req.end_time is not None:
        fields["end_time"] = _parse_time(req.end_time)
    if req.is_active is not None:
        fields["is_active"] = req.is_active

    updated = await repo.update_schedule_day(doctor_id, day_of_week, **fields)
    if not updated:
        raise HTTPException(
            status_code=404, detail="Schedule entry not found for this day"
        )

    await audit_log(
        db,
        actor=current_user["user_id"],
        action="update_schedule_day",
        entity_type="doctor_schedule",
        entity_id=doctor_id,
        details={"day_of_week": day_of_week},
    )

    return {
        "id": updated.id,
        "doctor_id": updated.doctor_id,
        "day_of_week": updated.day_of_week,
        "start_time": updated.start_time.isoformat(),
        "end_time": updated.end_time.isoformat(),
        "is_active": updated.is_active,
    }


@router.delete("/{doctor_id}/schedule/{day_of_week}")
async def delete_schedule_day(
    doctor_id: int,
    day_of_week: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    if day_of_week < 0 or day_of_week > 6:
        raise HTTPException(
            status_code=422, detail="day_of_week must be 0-6 (Monday-Sunday)"
        )

    repo = DoctorRepository(db)
    doctor = await repo.get_by_id(doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    deleted = await repo.delete_schedule_day(doctor_id, day_of_week)
    if not deleted:
        raise HTTPException(
            status_code=404, detail="Schedule entry not found for this day"
        )

    await audit_log(
        db,
        actor=current_user["user_id"],
        action="delete_schedule_day",
        entity_type="doctor_schedule",
        entity_id=doctor_id,
        details={"day_of_week": day_of_week},
    )

    return {"deleted": True, "doctor_id": doctor_id, "day_of_week": day_of_week}


@router.get("/{doctor_id}/appointments/today")
async def get_doctor_today_appointments(
    doctor_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = current_user.get("role", "patient")
    if role not in ("doctor", "admin"):
        raise HTTPException(status_code=403, detail="Doctor or admin access required")

    if role == "doctor":
        from sqlalchemy import select
        from app.models import Doctor

        doc_result = await db.execute(
            select(Doctor).where(Doctor.user_id == current_user.get("user_id"))
        )
        linked_doctor = doc_result.scalar_one_or_none()
        if not linked_doctor or linked_doctor.id != doctor_id:
            raise HTTPException(
                status_code=403, detail="Can only access own appointments"
            )

    repo = DoctorRepository(db)
    doctor = await repo.get_by_id(doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    appt_repo = AppointmentRepository(db)
    appts = await appt_repo.get_today_appointments(doctor_id)
    result = []
    for appt in appts:
        patient_repo = PatientRepository(db)
        patient = await patient_repo.get_by_id(appt.patient_id)
        result.append(
            {
                "id": appt.id,
                "patient_name": patient.name if patient else "Unknown",
                "appointment_time": appt.appointment_time.isoformat(),
                "duration_minutes": appt.duration_minutes,
                "status": appt.status.value,
                "notes": appt.notes,
            }
        )

    return {
        "doctor_id": doctor_id,
        "date": datetime.utcnow().date().isoformat(),
        "appointments": result,
    }


@router.get("/{doctor_id}/appointments/upcoming")
async def get_doctor_upcoming_appointments(
    doctor_id: int,
    days: int = Query(7, ge=1, le=30),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = current_user.get("role", "patient")
    if role not in ("doctor", "admin"):
        raise HTTPException(status_code=403, detail="Doctor or admin access required")

    if role == "doctor":
        from sqlalchemy import select
        from app.models import Doctor

        doc_result = await db.execute(
            select(Doctor).where(Doctor.user_id == current_user.get("user_id"))
        )
        linked_doctor = doc_result.scalar_one_or_none()
        if not linked_doctor or linked_doctor.id != doctor_id:
            raise HTTPException(
                status_code=403, detail="Can only access own appointments"
            )

    repo = DoctorRepository(db)
    doctor = await repo.get_by_id(doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    appt_repo = AppointmentRepository(db)
    appts = await appt_repo.get_upcoming_appointments(doctor_id, days=days)
    result = []
    for appt in appts:
        patient_repo = PatientRepository(db)
        patient = await patient_repo.get_by_id(appt.patient_id)
        result.append(
            {
                "id": appt.id,
                "patient_name": patient.name if patient else "Unknown",
                "appointment_time": appt.appointment_time.isoformat(),
                "duration_minutes": appt.duration_minutes,
                "status": appt.status.value,
                "notes": appt.notes,
            }
        )

    return {"doctor_id": doctor_id, "days": days, "appointments": result}


@router.get("/{doctor_id}/patients")
async def get_doctor_patients(
    doctor_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = current_user.get("role", "patient")
    if role not in ("doctor", "admin"):
        raise HTTPException(status_code=403, detail="Doctor or admin access required")

    if role == "doctor":
        from sqlalchemy import select
        from app.models import Doctor

        doc_result = await db.execute(
            select(Doctor).where(Doctor.user_id == current_user.get("user_id"))
        )
        linked_doctor = doc_result.scalar_one_or_none()
        if not linked_doctor or linked_doctor.id != doctor_id:
            raise HTTPException(status_code=403, detail="Can only access own patients")

    repo = DoctorRepository(db)
    doctor = await repo.get_by_id(doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    patient_repo = PatientRepository(db)
    patients = await patient_repo.get_patients_for_doctor(doctor_id)
    result = [
        {"id": p.id, "name": p.name, "email": p.email, "phone": p.phone}
        for p in patients
    ]

    return {"doctor_id": doctor_id, "patients": result}
