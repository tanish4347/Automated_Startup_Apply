from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from contextlib import asynccontextmanager
from typing import List

from src.db.session import engine, Base, get_db
from src.models.job import Job, Application
from src.api.schemas import JobCreate, JobResponse, ApplicationResponse
from src.core.queue import app_queue

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Start the application background worker
    app_queue.start_worker()

    yield

    # Shutdown worker
    await app_queue.stop_worker()

app = FastAPI(title="Automated Job Application System API", lifespan=lifespan)

@app.post("/jobs/", response_model=JobResponse)
async def create_job(job: JobCreate, db: AsyncSession = Depends(get_db)):
    # Check deduplication by original_url
    result = await db.execute(select(Job).where(Job.original_url == job.original_url))
    existing_job = result.scalar_one_or_none()
    if existing_job:
        raise HTTPException(status_code=400, detail="Job already exists")

    db_job = Job(**job.model_dump())
    db.add(db_job)
    await db.commit()
    await db.refresh(db_job)
    return db_job

@app.get("/jobs/", response_model=List[JobResponse])
async def list_jobs(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Job).offset(skip).limit(limit))
    return result.scalars().all()

@app.post("/jobs/{job_id}/apply", response_model=ApplicationResponse)
async def queue_application(job_id: int, db: AsyncSession = Depends(get_db)):
    # Verify job exists
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Create application record
    application = Application(job_id=job.id, status="QUEUED")
    db.add(application)
    await db.commit()
    await db.refresh(application)

    # Add to queue
    await app_queue.add_job(application.id)

    return application

from src.api.profile import router as profile_router
app.include_router(profile_router)
