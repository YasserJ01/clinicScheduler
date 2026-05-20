from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.models import Base

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_timeout=10,
    pool_recycle=1800,
    echo=False,
)

async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
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
        await conn.execute(text(f"""
            DO $$ BEGIN
                CREATE TYPE {enum_name} AS ENUM ({', '.join(f"'{v}'" for v in values)});
            EXCEPTION WHEN duplicate_object THEN null;
            END $$;
        """))
    except IntegrityError:
        pass


async def init_db():
    async with engine.begin() as conn:
        await _create_enum_if_not_exists(conn, "userrole", ["patient", "doctor", "admin"])
        await _create_enum_if_not_exists(conn, "appointmentstatus", ["scheduled", "confirmed", "completed", "cancelled"])
        await conn.run_sync(Base.metadata.create_all)
