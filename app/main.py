import logging
import signal
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.core.middleware import MessagePackMiddleware
from app.core.metrics_middleware import MetricsMiddleware
from app.core.exceptions import register_exception_handlers
from app.api.v1.routers import auth, doctors, patients, appointments, health, admin, metrics
from app.db.session import init_db, async_session_factory, engine

logging.basicConfig(level=logging.INFO)


async def seed_data():
    async with async_session_factory() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM doctors"))
        count = result.scalar()
        if count == 0:
            from app.models import Doctor
            session.add_all([
                Doctor(name="Dr. Smith", specialty="Cardiology"),
                Doctor(name="Dr. Jones", specialty="Dermatology"),
            ])
            await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_data()
    logger = logging.getLogger("clinic.main")
    logger.info("Application started")
    yield
    logger.info("Shutting down gracefully...")
    await engine.dispose()
    logger.info("Database connections closed")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Clinic Scheduler API",
        version="1.0.0",
        lifespan=lifespan,
    )

    cors_origins = [settings.FRONTEND_URL] if settings.FRONTEND_URL != "*" else ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(MessagePackMiddleware)
    app.add_middleware(MetricsMiddleware)

    register_exception_handlers(app)

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(doctors.router, prefix="/api/v1")
    app.include_router(patients.router, prefix="/api/v1")
    app.include_router(appointments.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(metrics.router, prefix="/api/v1")

    return app


app = create_app()
