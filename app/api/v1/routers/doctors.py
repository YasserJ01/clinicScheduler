import math
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.db.repository import DoctorRepository
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
