import hashlib
import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db.session import get_db
from app.models import ApiKey
from app.core.security import verify_password

security = HTTPBearer(auto_error=False)

_redis: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    if request and request.headers.get("X-API-Key"):
        return await _get_api_key_user(request.headers["X-API-Key"], db)

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

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
            "_auth_method": "jwt",
        }
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token decode error"
        )


async def _get_api_key_user(api_key: str, db: AsyncSession) -> dict:
    prefix = api_key[:8]
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.key_prefix == prefix,
            ApiKey.is_active.is_(True),
        )
    )
    matches = result.scalars().all()

    for key in matches:
        if key.expires_at and key.expires_at < __import__("datetime").datetime.utcnow():
            continue
        if verify_password(api_key, key.key_hash):
            return {
                "user_id": f"apikey:{key.name}",
                "role": key.role.value,
                "tenant_id": key.tenant_id,
                "_raw_token": api_key,
                "_auth_method": "api_key",
            }

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
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
