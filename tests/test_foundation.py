import pytest
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from src.db.session import Base
from src.models.job import Job

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False}
)
TestingSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)

@pytest.mark.asyncio(loop_scope="function")
async def test_db_models():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestingSessionLocal() as db:
        new_job = Job(
            source="test_source",
            original_url="http://test.com/1",
            company="Test Company",
            role="Test Role"
        )
        db.add(new_job)
        await db.commit()
        await db.refresh(new_job)

        assert new_job.id is not None
        assert new_job.company == "Test Company"
