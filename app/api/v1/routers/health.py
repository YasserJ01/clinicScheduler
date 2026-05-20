from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
import redis.asyncio as aioredis
from app.config import settings

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health_check(db: AsyncSession = Depends(get_db)):
    db_status = "healthy"
    redis_status = "healthy"

    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "unhealthy"

    try:
        r = aioredis.from_url(settings.REDIS_URL)
        await r.ping()
        await r.aclose()
    except Exception:
        redis_status = "unhealthy"

    status_code = 200 if db_status == "healthy" else 503
    return {
        "status": "ok" if status_code == 200 else "degraded",
        "database": db_status,
        "redis": redis_status,
    }
