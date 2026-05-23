import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from jose import jwt
from passlib.context import CryptContext
from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
refresh_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_access_token(
    subject: str | Any,
    expires_delta: timedelta | None = None,
    extra_claims: dict | None = None,
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode = {"sub": str(subject), "exp": expire}
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(subject: str | Any) -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = refresh_context.hash(raw_token)
    return raw_token, token_hash


def verify_refresh_token(raw_token: str, stored_hash: str) -> bool:
    return refresh_context.verify(raw_token, stored_hash)


def create_password_reset_token() -> tuple[str, str, str]:
    """Returns (full_token_for_client, jti, hash_for_db)."""
    jti = secrets.token_urlsafe(16)
    raw_secret = secrets.token_urlsafe(32)
    full_token = f"{jti}.{raw_secret}"
    token_hash = pwd_context.hash(raw_secret)
    return full_token, jti, token_hash


def verify_password_reset_token(full_token: str, stored_hash: str) -> bool:
    """Verify the secret portion against the stored hash."""
    try:
        _, raw_secret = full_token.split(".", 1)
        return pwd_context.verify(raw_secret, stored_hash)
    except Exception:
        return False


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)
