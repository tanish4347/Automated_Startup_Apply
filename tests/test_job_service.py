from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401  (registers all tables)
from autoapply.models.base import Base
from autoapply.models.job import WorkMode
from autoapply.services.job_service import ingest_job


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_ingest_guesses_work_mode_from_location():
    session = _session()
    remote = ingest_job(session, {"title": "ML Intern", "company": "A", "location": "Remote, India"})
    onsite = ingest_job(session, {"title": "SDE Intern", "company": "B", "location": "Mumbai"})
    assert remote.work_mode == WorkMode.REMOTE
    assert onsite.work_mode == WorkMode.ONSITE


def test_ingest_keeps_source_supplied_work_mode():
    session = _session()
    job = ingest_job(session, {"title": "DS Intern", "company": "C", "location": "Pune", "work_mode": "hybrid"})
    assert job.work_mode == WorkMode.HYBRID
