import contextvars
import logging
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from app.config import settings
from app.models import Base

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.POOL_SIZE,
    max_overflow=settings.MAX_OVERFLOW,
    pool_timeout=10,
    pool_recycle=1800,
    echo=False,
)

async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

read_engine = create_async_engine(
    settings.READ_DATABASE_URL or settings.DATABASE_URL,
    pool_size=settings.POOL_SIZE,
    max_overflow=settings.MAX_OVERFLOW,
    pool_timeout=10,
    pool_recycle=1800,
    echo=False,
)

read_session_factory = async_sessionmaker(
    read_engine, class_=AsyncSession, expire_on_commit=False
)

_tenant_ctx: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "rls_tenant_id", default=None
)
_role_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "rls_user_role", default=None
)


def set_rls_context(tenant_id: int | None = None, role: str | None = None) -> None:
    if tenant_id is not None:
        _tenant_ctx.set(tenant_id)
    if role is not None:
        _role_ctx.set(role)


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        tid = _tenant_ctx.get()
        role = _role_ctx.get()
        if tid or role:
            parts = []
            if tid:
                parts.append(f"app.current_tenant_id = '{tid}'")
            if role:
                parts.append(f"app.current_user_role = '{role}'")
            await session.execute(text(f"SET LOCAL {', '.join(parts)}"))
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_read_db() -> AsyncSession:
    async with read_session_factory() as session:
        tid = _tenant_ctx.get()
        role = _role_ctx.get()
        if tid or role:
            parts = []
            if tid:
                parts.append(f"app.current_tenant_id = '{tid}'")
            if role:
                parts.append(f"app.current_user_role = '{role}'")
            await session.execute(text(f"SET LOCAL {', '.join(parts)}"))
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def _create_enum_if_not_exists(conn, enum_name, values):
    """Create a PostgreSQL ENUM type if it doesn't already exist.

    asyncpg translates PostgreSQL duplicate_object errors into SQLAlchemy
    IntegrityError, so the PL/pgSQL EXCEPTION block doesn't work through
    SQLAlchemy. We catch the error at the Python level instead.
    """
    try:
        await conn.execute(
            text(f"""
            DO $$ BEGIN
                CREATE TYPE {enum_name} AS ENUM ({", ".join(f"'{v}'" for v in values)});
            EXCEPTION WHEN duplicate_object THEN null;
            END $$;
        """)
        )
    except IntegrityError:
        pass


async def _create_partial_unique_index(conn):
    """Create a partial unique index to prevent double-booking race conditions.

    This ensures that at the DB level, no two non-cancelled appointments can
    exist for the same (doctor_id, appointment_time) combination.
    """
    try:
        await conn.execute(
            text("""
            CREATE UNIQUE INDEX uix_appointment_slot
            ON appointments (doctor_id, appointment_time)
            WHERE status != 'cancelled';
        """)
        )
    except (IntegrityError, ProgrammingError):
        pass


async def _add_enum_value_if_not_exists(conn, enum_name, new_value):
    """Add a value to an existing PostgreSQL ENUM type.

    Uses a DO block with exception handling so that duplicate_value
    errors are caught inside PostgreSQL, preventing asyncpg transaction
    abort.
    """
    try:
        await conn.execute(
            text(f"""
            DO $$ BEGIN
                ALTER TYPE {enum_name} ADD VALUE '{new_value}';
            EXCEPTION WHEN duplicate_object THEN null;
            END $$;
        """)
        )
    except (IntegrityError, ProgrammingError):
        pass


async def init_db():
    if settings.ALEMBIC_ENABLED:
        await _run_alembic_migrations()
    else:
        async with engine.begin() as conn:
            await _create_enum_if_not_exists(
                conn, "userrole", ["patient", "doctor", "admin"]
            )
            await _add_enum_value_if_not_exists(conn, "userrole", "superadmin")
            await _create_enum_if_not_exists(
                conn,
                "appointmentstatus",
                ["scheduled", "confirmed", "completed", "cancelled"],
            )
            await conn.run_sync(Base.metadata.create_all)
            await _create_partial_unique_index(conn)

        # RLS setup in a separate transaction — the enum/index setup
        # above may abort the transaction on duplicate-object errors
        # that asyncpg treats as transaction-fatal even when caught
        # via Python-level try/except.
        try:
            async with engine.begin() as conn:
                await _enable_rls(conn)
        except Exception:
            logging.getLogger("clinic.main").exception(
                "RLS setup failed (non-fatal, continuing)"
            )


RLS_TABLES = [
    "tenants",
    "users",
    "doctors",
    "doctor_schedules",
    "patients",
    "appointments",
    "recurring_series",
    "audit_log",
    "webhooks",
    "webhook_deliveries",
    "api_keys",
]


async def _enable_rls(conn):
    for table in RLS_TABLES:
        for sql in [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"""
                CREATE POLICY tenant_isolation ON {table}
                    FOR ALL
                    USING (
                        tenant_id = COALESCE(
                            nullif(current_setting('app.current_tenant_id', true), ''),
                            '-1'
                        )::int
                    )
            """,
            f"""
                CREATE POLICY superadmin_bypass ON {table}
                    FOR ALL
                    USING (
                        current_setting('app.current_user_role', true) = 'superadmin'
                    )
            """,
        ]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(sql))
            except Exception:
                pass


async def _run_alembic_migrations():
    """Run Alembic migrations for production deployments."""
    from alembic.config import Config
    from alembic import command
    import os

    alembic_cfg = Config(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "alembic.ini")
    )
    alembic_cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    command.upgrade(alembic_cfg, "head")
