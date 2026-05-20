from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.db.repository import AppointmentRepository, DoctorRepository, PatientRepository
from app.api.v1.dependencies import get_current_user

router = APIRouter(prefix="/appointments", tags=["appointments"])


class AppointmentCreate(BaseModel):
    doctor_id: int
    patient_name: str
    appointment_time: datetime


class AppointmentResponse(BaseModel):
    id: int
    doctor_id: int
    patient_name: str
    appointment_time: datetime
    status: str

    model_config = {"from_attributes": True}


@router.get("", response_model=list[AppointmentResponse])
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
        result.append({
            "id": appt.id,
            "doctor_id": appt.doctor_id,
            "patient_name": patient.name if patient else "Unknown",
            "appointment_time": appt.appointment_time,
            "status": appt.status.value,
        })
    return result


@router.post("", response_model=AppointmentResponse, status_code=201)
async def create_appointment(
    appt: AppointmentCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doctor_repo = DoctorRepository(db)
    doctor = await doctor_repo.get_by_id(appt.doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    conflict = await AppointmentRepository(db).check_conflict(
        appt.doctor_id, appt.appointment_time
    )
    if conflict:
        raise HTTPException(status_code=409, detail="Time slot already booked")

    patient_repo = PatientRepository(db)
    patient = await patient_repo.get_or_create_by_name(
        appt.patient_name, f"{appt.patient_name.lower().replace(' ', '.')}@clinic.com"
    )

    new_appt = await AppointmentRepository(db).create(
        doctor_id=appt.doctor_id,
        patient_id=patient.id,
        appointment_time=appt.appointment_time,
    )

    return {
        "id": new_appt.id,
        "doctor_id": new_appt.doctor_id,
        "patient_name": patient.name,
        "appointment_time": new_appt.appointment_time,
        "status": new_appt.status.value,
    }


@router.get("/{appointment_id}", response_model=AppointmentResponse)
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
        "patient_name": patient.name if patient else "Unknown",
        "appointment_time": appt.appointment_time,
        "status": appt.status.value,
    }
