from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = (
        "postgresql+asyncpg://clinic:clinicpass@localhost:5432/clinic_db"
    )
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    LOG_LEVEL: str = "info"
    FRONTEND_URL: str = "*"
    ALEMBIC_ENABLED: bool = False
    CHAOS_ENABLED: bool = False
    EMAIL_PROVIDER: str = "null"
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 1025
    SENDGRID_API_KEY: str = ""
    FROM_EMAIL: str = "clinic@example.com"

    POOL_SIZE: int = 15
    MAX_OVERFLOW: int = 5

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
