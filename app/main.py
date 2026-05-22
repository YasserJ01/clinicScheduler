import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.core.middleware import MessagePackMiddleware
from app.core.metrics_middleware import MetricsMiddleware
from app.core.request_id_middleware import RequestIDMiddleware
from app.core.deprecation_middleware import DeprecationMiddleware
from app.core.tenant_middleware import TenantMiddleware
from app.core.exceptions import register_exception_handlers
from app.api.v1.routers import (
    auth,
    doctors,
    patients,
    appointments,
    health,
    admin,
    metrics,
    analytics,
)
from app.api.v2.routers import appointments as appointments_v2, doctors as doctors_v2
from app.db.session import init_db, async_session_factory, engine

logging.basicConfig(level=logging.INFO)


async def seed_data():
    async with async_session_factory() as session:
        from app.models import Doctor, Tenant

        result = await session.execute(text("SELECT COUNT(*) FROM tenants"))
        tenant_count = result.scalar()
        if tenant_count == 0:
            session.add(Tenant(name="Default Clinic", slug="default"))
            await session.commit()

        result = await session.execute(text("SELECT COUNT(*) FROM doctors"))
        count = result.scalar()
        if count == 0:
            session.add_all(
                [
                    Doctor(name="Dr. Smith", specialty="Cardiology", tenant_id=1),
                    Doctor(name="Dr. Jones", specialty="Dermatology", tenant_id=1),
                ]
            )
            await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_data()

    if os.getenv("ENABLE_TELEMETRY", "false").lower() == "true":
        try:
            from app.core.telemetry import setup_telemetry

            setup_telemetry(app)
        except Exception as e:
            logging.getLogger("clinic.main").warning("Telemetry init failed: %s", e)

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
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(DeprecationMiddleware)
    app.add_middleware(TenantMiddleware)

    register_exception_handlers(app)

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(doctors.router, prefix="/api/v1")
    app.include_router(patients.router, prefix="/api/v1")
    app.include_router(appointments.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(metrics.router, prefix="/api/v1")
    app.include_router(analytics.router, prefix="/api/v1")

    app.include_router(appointments_v2.router, prefix="/api/v2")
    app.include_router(doctors_v2.router, prefix="/api/v2")

    return app


app = create_app()
