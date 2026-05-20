from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.db.repository import PatientRepository
from app.api.v1.dependencies import get_current_user

router = APIRouter(prefix="/patients", tags=["patients"])


class PatientResponse(BaseModel):
    id: int
    name: str
    email: str

    model_config = {"from_attributes": True}


@router.get("", response_model=list[PatientResponse])
async def list_patients(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = PatientRepository(db)
    patients = await repo.list_all()
    return [{"id": p.id, "name": p.name, "email": p.email} for p in patients]


@router.get("/me", response_model=PatientResponse)
async def get_my_profile(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return {"id": 0, "name": current_user["user_id"], "email": f"{current_user['user_id']}@clinic.com"}
