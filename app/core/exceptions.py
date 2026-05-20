import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from app.core.circuit_breaker import CircuitBreakerError

logger = logging.getLogger("clinic.exceptions")


def register_exception_handlers(app: FastAPI):
    @app.exception_handler(CircuitBreakerError)
    async def circuit_breaker_handler(request: Request, exc: CircuitBreakerError):
        logger.error("Circuit breaker error: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"error": "Service temporarily unavailable", "detail": str(exc)},
        )

    @app.exception_handler(SQLAlchemyError)
    async def db_error_handler(request: Request, exc: SQLAlchemyError):
        logger.error("Database error: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Database error", "detail": str(exc)},
        )

    @app.exception_handler(Exception)
    async def general_handler(request: Request, exc: Exception):
        logger.error("Unhandled error: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "detail": str(exc)},
        )
