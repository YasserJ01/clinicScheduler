import hashlib
import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from app.config import settings
from app.db.session import get_db
from sqlalchemy.ext.asyncio import AsyncSession

security = HTTPBearer()

_redis: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            )
        redis = await _get_redis()
        jti = hashlib.sha256(
            f"{user_id}:{credentials.credentials}".encode()
        ).hexdigest()
        denied = await redis.get(f"token_denylist:{jti}")
        if denied:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked"
            )
        return {
            "user_id": user_id,
            "role": payload.get("role", "patient"),
            "tenant_id": payload.get("tenant_id"),
            "_raw_token": credentials.credentials,
        }
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token decode error"
        )


async def get_current_tenant(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> int:
    header_tenant = request.state.tenant_id
    user_tenant = current_user.get("tenant_id")

    if header_tenant and user_tenant and header_tenant != user_tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant mismatch: header does not match token",
        )

    tenant_id = header_tenant or user_tenant
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant ID required. Set X-Tenant-ID header or include tenant in token.",
        )

    return tenant_id
