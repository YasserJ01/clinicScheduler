from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.db.repository import DoctorRepository
from app.api.v1.dependencies import get_current_user

router = APIRouter(prefix="/doctors", tags=["doctors"])


class DoctorCreate(BaseModel):
    name: str
    specialty: str


class DoctorResponse(BaseModel):
    id: int
    name: str
    specialty: str

    model_config = {"from_attributes": True}


@router.get("", response_model=list[DoctorResponse])
async def list_doctors(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = DoctorRepository(db)
    doctors = await repo.list_all()
    return [{"id": d.id, "name": d.name, "specialty": d.specialty} for d in doctors]


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
    }
