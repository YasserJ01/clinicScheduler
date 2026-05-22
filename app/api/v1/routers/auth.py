import hashlib
import redis.asyncio as aioredis
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.db.repository import PatientRepository, UserRepository
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
    email: str | None = None

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


_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


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
    refresh_sha256 = hashlib.sha256(raw_refresh.encode()).hexdigest()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    ).replace(tzinfo=None)
    await db.execute(select(User).where(User.id == user.id))
    user.refresh_token_hash = refresh_hash
    user.refresh_token_sha256 = refresh_sha256
    user.refresh_token_expires_at = expires_at
    await db.flush()

    if req.role == "patient":
        patient_repo = PatientRepository(db)
        patient_email = req.email or f"{req.username}@clinic.com"
        await patient_repo.get_or_create_by_email(
            name=req.username,
            email=patient_email,
            tenant_id=req.tenant_id,
            user_id=user.id,
        )
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
    refresh_sha256 = hashlib.sha256(raw_refresh.encode()).hexdigest()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    ).replace(tzinfo=None)
    user.refresh_token_hash = refresh_hash
    user.refresh_token_sha256 = refresh_sha256
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
    lookup = hashlib.sha256(req.refresh_token.encode()).hexdigest()
    result = await db.execute(select(User).where(User.refresh_token_sha256 == lookup))
    matched_user = result.scalar_one_or_none()
    if not matched_user:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if not verify_refresh_token(req.refresh_token, matched_user.refresh_token_hash):
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if (
        matched_user.refresh_token_expires_at
        and matched_user.refresh_token_expires_at < datetime.utcnow()
    ):
        raise HTTPException(status_code=401, detail="Refresh token expired")
    new_raw, new_hash = create_refresh_token(matched_user.username)
    new_sha256 = hashlib.sha256(new_raw.encode()).hexdigest()
    new_expires = (
        datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    ).replace(tzinfo=None)
    matched_user.refresh_token_hash = new_hash
    matched_user.refresh_token_sha256 = new_sha256
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
            ttl = int(exp - datetime.now(timezone.utc).timestamp())
            if ttl > 0:
                jti = hashlib.sha256(
                    (
                        current_user.get("user_id", "")
                        + ":"
                        + current_user.get("_raw_token", "")
                    ).encode()
                ).hexdigest()
                await redis.set(f"token_denylist:{jti}", "1", ex=ttl)
    except Exception:
        pass
    return {"message": "Logged out successfully"}
