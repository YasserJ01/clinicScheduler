from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://clinic:clinicpass@localhost:5432/clinic_db"
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    LOG_LEVEL: str = "info"
    FRONTEND_URL: str = "*"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
