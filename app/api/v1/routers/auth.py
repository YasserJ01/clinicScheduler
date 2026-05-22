import redis.asyncio as aioredis
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.db.repository import UserRepository
from app.models import User
from app.api.v1.dependencies import get_current_user
from app.core.security import (
    create_access_token,
    create_refresh_token,
    verify_password,
    verify_refresh_token,
)
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "patient"
    tenant_id: int = 1

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password must not exceed 72 bytes (bcrypt limit)")
        return v


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


def _get_redis():
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


@router.post("/register", response_model=TokenResponse)
async def register(
    req: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepository(db)
    existing = await repo.get_by_username(req.username, tenant_id=req.tenant_id)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    user = await repo.create(
        username=req.username,
        password=req.password,
        role=req.role,
        tenant_id=req.tenant_id,
    )
    raw_refresh, refresh_hash = create_refresh_token(user.username)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    ).replace(tzinfo=None)
    await db.execute(select(User).where(User.id == user.id))
    user.refresh_token_hash = refresh_hash
    user.refresh_token_expires_at = expires_at
    await db.flush()
    access_token = create_access_token(
        subject=user.username,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        extra_claims={"role": req.role, "tenant_id": req.tenant_id},
    )
    return TokenResponse(access_token=access_token, refresh_token=raw_refresh)


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepository(db)
    user = await repo.get_by_username(req.username)
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    raw_refresh, refresh_hash = create_refresh_token(user.username)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    ).replace(tzinfo=None)
    user.refresh_token_hash = refresh_hash
    user.refresh_token_expires_at = expires_at
    await db.flush()
    access_token = create_access_token(
        subject=user.username,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        extra_claims={"role": user.role.value, "tenant_id": user.tenant_id},
    )
    return TokenResponse(access_token=access_token, refresh_token=raw_refresh)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    req: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.refresh_token_hash.isnot(None)))
    users = result.scalars().all()
    matched_user = None
    for u in users:
        if u.refresh_token_hash and verify_refresh_token(
            req.refresh_token, u.refresh_token_hash
        ):
            if u.refresh_token_expires_at and u.refresh_token_expires_at < datetime.utcnow():
                raise HTTPException(status_code=401, detail="Refresh token expired")
            matched_user = u
            break
    if not matched_user:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    new_raw, new_hash = create_refresh_token(matched_user.username)
    new_expires = (
        datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    ).replace(tzinfo=None)
    matched_user.refresh_token_hash = new_hash
    matched_user.refresh_token_expires_at = new_expires
    await db.flush()
    access_token = create_access_token(
        subject=matched_user.username,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        extra_claims={
            "role": matched_user.role.value,
            "tenant_id": matched_user.tenant_id,
        },
    )
    return TokenResponse(access_token=access_token, refresh_token=new_raw)


@router.post("/logout")
async def logout(
    current_user: dict = Depends(get_current_user),
):
    redis = _get_redis()
    try:
        from jose import jwt

        payload = jwt.decode(
            current_user.get("_raw_token", ""),
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"verify_exp": False},
        )
        exp = payload.get("exp")
        if exp:
            from datetime import datetime, timezone

            ttl = int(exp - datetime.now(timezone.utc).timestamp())
            if ttl > 0:
                jti = (
                    current_user.get("user_id", "")
                    + ":"
                    + current_user.get("_raw_token", "")[:8]
                )
                await redis.set(f"token_denylist:{jti}", "1", ex=ttl)
    except Exception:
        pass
    finally:
        await redis.aclose()
    return {"message": "Logged out successfully"}
