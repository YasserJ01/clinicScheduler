from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text
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


async def init_db():
    async with engine.begin() as conn:
        await conn.execute(text("""
            DO $$ BEGIN
                CREATE TYPE userrole AS ENUM ('patient', 'doctor', 'admin');
            EXCEPTION WHEN duplicate_object THEN null;
            END $$;
        """))
        await conn.execute(text("""
            DO $$ BEGIN
                CREATE TYPE appointmentstatus AS ENUM ('scheduled', 'confirmed', 'completed', 'cancelled');
            EXCEPTION WHEN duplicate_object THEN null;
            END $$;
        """))
        await conn.run_sync(Base.metadata.create_all)
