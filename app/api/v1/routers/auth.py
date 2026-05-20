from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.db.repository import UserRepository
from app.core.security import create_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "patient"


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/register", response_model=TokenResponse)
async def register(
    req: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepository(db)
    existing = await repo.get_by_username(req.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    user = await repo.create(username=req.username, password=req.password, role=req.role)
    token = create_access_token(subject=user.username, expires_delta=timedelta(minutes=30))
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepository(db)
    user = await repo.get_by_username(req.username)
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(subject=user.username, expires_delta=timedelta(minutes=30))
    return TokenResponse(access_token=token)
