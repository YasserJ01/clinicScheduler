import math
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.db.repository import AppointmentRepository, PatientRepository
from app.api.v1.dependencies import get_current_user

router = APIRouter(prefix="/appointments", tags=["appointments-v2"])


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
    )
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
                "notes": appt.notes,
                "series_id": appt.series_id,
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


def _parse_time_slot(time_slot: str | None):
    if not time_slot:
        return None
    from datetime import datetime

    dt = datetime.fromisoformat(time_slot.replace("Z", "+00:00"))
    return dt.replace(tzinfo=None) if dt.tzinfo else dt
