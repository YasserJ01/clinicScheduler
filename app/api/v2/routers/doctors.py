import math
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.db.repository import DoctorRepository
from app.api.v1.dependencies import get_current_user

router = APIRouter(prefix="/doctors", tags=["doctors-v2"])


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
    items = []
    for d in doctors:
        schedules = await repo.get_schedule(d.id)
        schedule_summary = [
            {
                "day_of_week": s.day_of_week,
                "start_time": s.start_time.isoformat(),
                "end_time": s.end_time.isoformat(),
            }
            for s in schedules
        ]
        items.append(
            {
                "id": d.id,
                "name": d.name,
                "specialty": d.specialty,
                "is_active": d.is_active,
                "schedule": schedule_summary,
            }
        )
    pages = math.ceil(total / page_size) if total > 0 else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }
