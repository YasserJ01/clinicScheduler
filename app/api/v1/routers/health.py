from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
import redis.asyncio as aioredis
from app.config import settings
from app.core.circuit_breaker import db_breaker, redis_breaker, CircuitBreakerError
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health_check(db: AsyncSession = Depends(get_db)):
    db_status = "healthy"
    redis_status = "healthy"

    try:
        await db_breaker.call(db.execute, text("SELECT 1"))
    except CircuitBreakerError:
        logger.error("health_check: DB circuit breaker is OPEN")
        db_status = "unhealthy"
    except Exception as e:
        logger.error(f"health_check: DB probe failed: {e}")
        db_status = "unhealthy"

    try:
        r = aioredis.from_url(settings.REDIS_URL)
        await redis_breaker.call(r.ping)
        await r.aclose()
    except CircuitBreakerError:
        logger.error("health_check: Redis circuit breaker is OPEN")
        redis_status = "unhealthy"
    except Exception as e:
        logger.error(f"health_check: Redis probe failed: {e}")
        redis_status = "unhealthy"

    status_code = 200 if db_status == "healthy" else 503
    return {
        "status": "ok" if status_code == 200 else "degraded",
        "database": db_status,
        "redis": redis_status,
    }
