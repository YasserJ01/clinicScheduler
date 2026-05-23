import json
import logging
import re
import redis.asyncio as aioredis
from datetime import datetime, timezone
from jose import jwt as jose_jwt
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from app.config import settings

logger = logging.getLogger("clinic.rate_limiter")

RATE_LIMIT_REQUESTS = 100
RATE_LIMIT_WINDOW = 60

PUBLIC_PATHS = re.compile(
    r"^/(api/v1/(auth/login|auth/register|auth/forgot-password|auth/reset-password|health|docs|redoc|openapi\.json))"
)

_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


def _extract_user_id(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer ") :]
    try:
        payload = jose_jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return payload.get("sub")
    except Exception:
        return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        if PUBLIC_PATHS.match(request.url.path):
            return await call_next(request)

        user_id = _extract_user_id(request)
        if not user_id:
            return await call_next(request)

        redis = _get_redis()
        now = datetime.now(timezone.utc).timestamp()
        window_start = now - RATE_LIMIT_WINDOW
        key = f"ratelimit:{user_id}"

        called_next = False
        response = None
        try:
            await redis.zremrangebyscore(key, 0, window_start)
            count = await redis.zcard(key)

            if count is not None and count >= RATE_LIMIT_REQUESTS:
                oldest = await redis.zrange(key, 0, 0, withscores=True)
                retry_after = 0
                if oldest:
                    retry_after = int((oldest[0][1] + RATE_LIMIT_WINDOW) - now)
                    if retry_after < 0:
                        retry_after = 1

                logger.warning(
                    "Rate limit exceeded: user=%s count=%d limit=%d",
                    user_id,
                    count,
                    RATE_LIMIT_REQUESTS,
                )
                return Response(
                    status_code=429,
                    content=json.dumps(
                        {
                            "detail": f"Rate limit exceeded. Try again in {retry_after} seconds."
                        }
                    ),
                    media_type="application/json",
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(RATE_LIMIT_REQUESTS),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(now) + retry_after),
                    },
                )

            member = f"{now}:{id(request)}"
            await redis.zadd(key, {member: now})
            await redis.expire(key, RATE_LIMIT_WINDOW * 2)

            remaining = RATE_LIMIT_REQUESTS - count - 1
            called_next = True
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_REQUESTS)
            response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
            response.headers["X-RateLimit-Reset"] = str(int(now) + RATE_LIMIT_WINDOW)
        except Exception:
            logger.exception("Rate limiter error, allowing request through")
            if not called_next:
                response = await call_next(request)

        return response
