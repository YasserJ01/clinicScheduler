import math
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from app.db.session import get_db, get_read_db
from app.db.repository import PatientRepository
from app.models import Patient, User
from app.api.v1.dependencies import get_current_user
from app.core.audit import audit_log

router = APIRouter(prefix="/patients", tags=["patients"])


class PatientCreate(BaseModel):
    name: str
    email: str
    phone: str | None = None


class PatientUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None


class PatientResponse(BaseModel):
    id: int
    name: str
    email: str

    model_config = {"from_attributes": True}


@router.post("", status_code=201, response_model=PatientResponse)
async def create_patient(
    req: PatientCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.get("tenant_id", 1)
    username_from_email = req.email.split("@")[0] if "@" in req.email else None
    user_pk = None
    if username_from_email:
        result = await db.execute(
            select(User.id).where(User.username == username_from_email)
        )
        user_pk = result.scalar_one_or_none()
    repo = PatientRepository(db)
    try:
        patient = await repo.get_or_create_by_email(
            req.name, req.email, tenant_id=tenant_id, user_id=user_pk
        )
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A patient with this email already exists",
        )
    return {"id": patient.id, "name": patient.name, "email": patient.email}


@router.get("")
async def list_patients(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_db),
):
    tenant_id = current_user.get("tenant_id")
    repo = PatientRepository(db)
    patients, total = await repo.list_paginated(
        page=page, page_size=page_size, search=search, tenant_id=tenant_id
    )
    items = [{"id": p.id, "name": p.name, "email": p.email} for p in patients]
    pages = math.ceil(total / page_size) if total > 0 else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.get("/me", response_model=PatientResponse)
async def get_my_profile(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.get("tenant_id", 1)
    username = current_user.get("user_id")
    result = await db.execute(select(User.id).where(User.username == username))
    user_pk = result.scalar_one_or_none()
    if user_pk is None:
        raise HTTPException(status_code=404, detail="User not found")
    patient_result = await db.execute(
        select(Patient).where(
            Patient.user_id == user_pk,
            Patient.tenant_id == tenant_id,
        )
    )
    patient = patient_result.scalars().first()
    if not patient:
        patient_result = await db.execute(
            select(Patient).where(
                Patient.email == f"{username}@clinic.com",
                Patient.tenant_id == tenant_id,
            )
        )
        patient = patient_result.scalar_one_or_none()
    if not patient:
        patient = Patient(
            name=username,
            email=f"{username}@clinic.com",
            tenant_id=tenant_id,
            user_id=user_pk,
        )
        db.add(patient)
        await db.flush()
    return {"id": patient.id, "name": patient.name, "email": patient.email}


@router.get("/{patient_id}", response_model=PatientResponse)
async def get_patient(
    patient_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_db),
):
    role = current_user.get("role", "patient")
    if role not in ("admin", "doctor"):
        raise HTTPException(status_code=403, detail="Admin or doctor access required")
    tenant_id = current_user.get("tenant_id")
    repo = PatientRepository(db)
    patient = await repo.get_by_id(patient_id, tenant_id=tenant_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"id": patient.id, "name": patient.name, "email": patient.email}


@router.patch("/{patient_id}", response_model=PatientResponse)
async def update_patient(
    patient_id: int,
    req: PatientUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    tenant_id = current_user.get("tenant_id")
    repo = PatientRepository(db)
    patient = await repo.get_by_id(patient_id, tenant_id=tenant_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    try:
        updated = await repo.update(
            patient_id, name=req.name, email=req.email, phone=req.phone
        )
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Email already exists for another patient"
        )
    if not updated:
        raise HTTPException(status_code=404, detail="Patient not found")
    await audit_log(
        db,
        actor=current_user["user_id"],
        action="update_patient",
        entity_type="patient",
        entity_id=patient_id,
        details={"name": req.name, "email": req.email, "phone": req.phone},
    )
    return {"id": updated.id, "name": updated.name, "email": updated.email}
