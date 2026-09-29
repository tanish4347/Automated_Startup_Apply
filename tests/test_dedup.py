import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401  (registers all tables)
from autoapply.models.application import ApplicationStatus
from autoapply.models.base import Base
from autoapply.models.job import Job, PayStatus, WorkMode
from autoapply.services.dedup import (
    compute_dedup_hash, deduplicate_job, normalize_company, normalize_title,
)
from autoapply.services.job_service import ingest_job


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# ── Key normalization ──────────────────────────────────────────────────────

@pytest.mark.parametrize("a, b", [
    ("SDE Intern", "SDE Internship"),
    ("SDE Intern", "SDE Intern (Remote)"),
    ("SDE Intern - Bangalore", "SDE Intern"),
    ("SDE-Intern", "sde intern"),
    ("Software Engineer, Intern", "Intern - Software Engineer"),
    ("Data Science -Intern", "Data Science Intern"),
])
def test_title_noise_is_ignored(a, b):
    assert normalize_title(a) == normalize_title(b)


def test_generic_company_words_are_only_dropped_from_legal_names():
    assert normalize_company("Zeta Labs") == "zetalabs"
    assert normalize_company("Zeta Labs Pvt Ltd") == "zeta"  # documented tradeoff


# Each pair below was wrongly merged in a real 20k-job run before dedup v2 was tightened.
@pytest.mark.parametrize("a, b", [
    ("Software Engineer Internship, Frontend", "Software Engineer, Frontend"),  # intern vs full-time
    ("Software Engineer Intern (Summer 2027)", "Software Engineer Intern (Winter 2027)"),
    ("Software Engineer Intern - Berlin (2027)", "Software Engineer Intern - Berlin (2026)"),
    ("SDE Intern", "Summer 2027 SDE Intern"),
])
def test_distinct_roles_and_cohorts_stay_distinct(a, b):
    assert normalize_title(a) != normalize_title(b)


def test_unrecognized_cities_are_not_collapsed():
    assert compute_dedup_hash("Software Engineer, Intern", "X", "Bucharest") != \
        compute_dedup_hash("Software Engineer, Intern", "X", "Chicago, IL")
    assert compute_dedup_hash("AI Trainer Intern", "Y", "Uran, Maharashtra, India") != \
        compute_dedup_hash("AI Trainer Intern", "Y", "Shahapur, Maharashtra, India")


def test_titles_that_differ_in_role_stay_distinct():
    assert normalize_title("Backend Intern") != normalize_title("Frontend Intern")


@pytest.mark.parametrize("name", ["Razorpay", "Razorpay Software Pvt. Ltd.", "RAZORPAY Private Limited", "razorpay inc"])
def test_company_legal_suffixes_are_ignored(name):
    assert normalize_company(name) == "razorpay"


# ── Hash behaviour ─────────────────────────────────────────────────────────

def test_same_job_across_sources_collapses():
    # LinkedIn: slug id, city in location. ATS: numeric id, city in the title.
    linkedin = compute_dedup_hash("SDE Intern", "Razorpay", "Bengaluru, Karnataka, India")
    ats = compute_dedup_hash("SDE Intern - Bangalore", "Razorpay Software Pvt Ltd", "Bangalore")
    assert linkedin == ats


def test_same_title_in_two_indian_cities_are_distinct_jobs():
    # AUDIT 2.2: every Indian city used to bucket to 'india'.
    assert compute_dedup_hash("SDE Intern", "Razorpay", "Bengaluru, India") != \
        compute_dedup_hash("SDE Intern", "Razorpay", "Pune, India")


def test_remote_and_onsite_versions_are_distinct():
    assert compute_dedup_hash("SDE Intern", "Groww", "Remote") != compute_dedup_hash("SDE Intern", "Groww", "Bengaluru")


def test_remote_variants_collapse():
    assert compute_dedup_hash("ML Intern", "Sarvam", "Remote (India)") == \
        compute_dedup_hash("ML Intern", "Sarvam", "Work from home - India")
    assert compute_dedup_hash("ML Intern", "Sarvam", "Remote") == compute_dedup_hash("ML Intern", "Sarvam", "WFH")


def test_remote_in_different_countries_stays_distinct():
    assert compute_dedup_hash("ML Intern", "X", "Remote - India") != compute_dedup_hash("ML Intern", "X", "US - Remote")


def test_different_companies_do_not_collapse():
    assert compute_dedup_hash("SDE Intern", "Razorpay", "Remote") != compute_dedup_hash("SDE Intern", "CRED", "Remote")


# ── Ingest: sightings and merge ────────────────────────────────────────────

def test_deduplicate_job_finds_the_stored_row(session):
    stored = ingest_job(session, {"title": "ML Intern", "company": "Groww", "location": "Remote"})
    _, dup = deduplicate_job(session, "ML Intern", "Groww", "Remote")
    assert dup is not None and dup.id == stored.id


def test_second_sighting_is_recorded_and_fills_missing_fields(session):
    linkedin = ingest_job(session, {
        "title": "SDE Intern", "company": "Razorpay", "location": "Bengaluru, Karnataka, India",
        "source": "linkedin", "source_url": "https://www.linkedin.com/jobs/view/123",
        "application_url": "https://www.linkedin.com/jobs/view/123",
        "classification_status": "AUTO_ACCEPT", "location_fit": "outside_policy", "apply_channel": "unknown",
    })
    ats = ingest_job(session, {
        "title": "SDE Intern - Bangalore", "company": "Razorpay", "location": "Bangalore",
        "source": "career_pages", "application_url": "https://boards.greenhouse.io/razorpay/jobs/1",
        "description_text": "Build payment APIs.", "ats_platform": "greenhouse", "apply_channel": "ats_direct",
        "pay_status": "PAID", "pay_evidence": "₹40,000/month", "stipend_min": 40000.0,
        "stipend_currency": "INR", "stipend_period": "month", "work_mode": "onsite",
    })

    assert ats.id == linkedin.id
    assert session.query(Job).count() == 1
    job = session.get(Job, linkedin.id)
    assert {s.source for s in job.sightings} == {"linkedin", "career_pages"}
    assert job.description_text == "Build payment APIs."
    assert (job.pay_status, job.stipend_min) == (PayStatus.PAID, 40000.0)
    # The ATS link replaces the LinkedIn link, since an applier can use it.
    assert job.apply_channel == "ats_direct"
    assert job.application_url == "https://boards.greenhouse.io/razorpay/jobs/1"
    assert job.ats_platform == "greenhouse"
    assert job.company_id is not None


def test_merge_never_overwrites_existing_values(session):
    ingest_job(session, {"title": "DS Intern", "company": "Meesho", "location": "Remote",
                         "description_text": "original", "source": "a"})
    job = ingest_job(session, {"title": "DS Intern", "company": "Meesho", "location": "Remote",
                               "description_text": "other", "source": "b"})
    assert job.description_text == "original"


def test_repeat_sighting_from_same_source_is_not_duplicated(session):
    data = {"title": "ML Intern", "company": "Zepto", "location": "Mumbai", "source": "linkedin",
            "source_url": "https://www.linkedin.com/jobs/view/9"}
    ingest_job(session, data)
    job = ingest_job(session, data)
    assert len(job.sightings) == 1


def test_ats_link_upgrade_updates_the_pending_application(session):
    ingest_job(session, {"title": "SDE Intern", "company": "CRED", "location": "Mumbai", "source": "linkedin",
                        "application_url": "https://www.linkedin.com/jobs/view/7",
                        "classification_status": "AUTO_ACCEPT", "location_fit": "ok"})
    job = ingest_job(session, {"title": "SDE Intern", "company": "CRED", "location": "Mumbai",
                               "source": "career_pages", "apply_channel": "ats_direct",
                               "application_url": "https://jobs.lever.co/cred/1"})
    [app] = job.applications
    assert app.status == ApplicationStatus.DISCOVERED
    assert app.application_url == "https://jobs.lever.co/cred/1"


def test_outside_policy_jobs_are_stored_but_not_queued(session):
    job = ingest_job(session, {"title": "SDE Intern", "company": "Swiggy", "location": "Bengaluru",
                               "classification_status": "AUTO_ACCEPT", "location_fit": "outside_policy"})
    assert job.is_active == 1
    assert job.applications == []


def test_ingest_links_the_company(session):
    job = ingest_job(session, {"title": "SDE Intern", "company": "Postman", "location": "Remote",
                               "source": "linkedin"})
    assert job.company_ref.name == "Postman"
    assert job.company_ref.discovered_via == "linkedin"
    assert job.work_mode == WorkMode.REMOTE
