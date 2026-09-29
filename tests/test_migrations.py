from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from autoapply.models.base import Base, engine_from_settings
from autoapply.models.migrate import _config, upgrade_db


def _url(tmp_path):
    return f"sqlite:///{tmp_path / 'test.db'}"


def test_fresh_db_upgrades_to_models_with_no_drift(tmp_path):
    url = _url(tmp_path)
    upgrade_db(url)
    engine = engine_from_settings(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models and migrations disagree: {diff}"


def test_pre_alembic_db_is_stamped_then_upgraded(tmp_path):
    url = _url(tmp_path)
    command.upgrade(_config(url), "0001_baseline")
    engine = engine_from_settings(url)
    with engine.begin() as conn:  # simulate a DB made by the old create_all
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text(
            "INSERT INTO jobs (source, title, is_active, discovered_at, created_at, updated_at) "
            "VALUES ('x', 'kept', 1, '2026-01-01', '2026-01-01', '2026-01-01')"
        ))
    upgrade_db(url)
    cols = {c["name"] for c in inspect(engine).get_columns("jobs")}
    assert "location_fit" in cols
    with engine.connect() as conn:
        assert conn.execute(text("SELECT title FROM jobs")).scalar() == "kept"


def test_sqlite_runs_in_wal_mode_with_busy_timeout(tmp_path):
    engine = engine_from_settings(_url(tmp_path))
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_backfill_links_companies_adds_sightings_and_merges_v2_duplicates(tmp_path):
    url = _url(tmp_path)
    cfg = _config(url)
    command.upgrade(cfg, "0002_location_fit")
    engine = engine_from_settings(url)
    ins = ("INSERT INTO jobs (source, source_url, application_url, title, company, location, work_mode, "
           "pay_status, description_text, is_active, classification_status, dedup_hash, discovered_at, "
           "created_at, updated_at) "
           "VALUES (:src, :url, :url, :title, :co, :loc, 'UNKNOWN', 'UNKNOWN', :desc, 1, 'AUTO_ACCEPT', :h, "
           "'2026-01-01', '2026-01-01', '2026-01-01')")
    with engine.begin() as conn:
        # Old dedup kept these apart (different source_id-based hashes); v2 treats them as one job.
        conn.execute(text(ins), dict(src="linkedin", url="https://linkedin.com/1", title="SDE Intern",
                                     co="Razorpay", loc="Bengaluru, India", desc=None, h="old1"))
        conn.execute(text(ins), dict(src="career_pages", url="https://boards.greenhouse.io/r/1",
                                     title="SDE Intern - Bangalore", co="Razorpay", loc="Bangalore",
                                     desc="Stipend ₹40,000/month", h="old2"))
        conn.execute(text("INSERT INTO applications (job_id, status, date_discovered, created_at, updated_at, "
                          "retry_count, max_retries) VALUES (2, 'DISCOVERED', '2026-01-01', '2026-01-01', "
                          "'2026-01-01', 0, 3)"))
    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        jobs = conn.execute(text("SELECT id, is_active, classification_status, dedup_hash, city, company_id, "
                                 "stipend_min, description_text, apply_channel, application_url "
                                 "FROM jobs ORDER BY id")).mappings().all()
        assert jobs[0]["is_active"] == 1 and jobs[0]["dedup_hash"] is not None
        assert jobs[0]["city"] == "Bengaluru" and jobs[0]["company_id"] is not None
        # The kept (LinkedIn) row inherits what only the ATS copy had.
        assert (jobs[0]["stipend_min"], jobs[0]["description_text"]) == (40000, "Stipend ₹40,000/month")
        assert jobs[0]["apply_channel"] == "ats_direct"
        assert jobs[0]["application_url"] == "https://boards.greenhouse.io/r/1"
        assert (jobs[1]["is_active"], jobs[1]["classification_status"], jobs[1]["dedup_hash"]) == (0, "MERGED", None)
        assert jobs[1]["stipend_min"] == 40000
        sources = {r[0] for r in conn.execute(text("SELECT source FROM job_sightings WHERE job_id = 1"))}
        assert sources == {"linkedin", "career_pages"}
        assert conn.execute(text("SELECT status FROM applications")).scalar() == "CLOSED"
        assert conn.execute(text("SELECT count(*) FROM companies")).scalar() == 1
