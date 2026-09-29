import pytest

from autoapply.config import LocationPolicy, load_search_config
from autoapply.sources.filter import (
    classify_role_family, detect_apply_channel, detect_duration_months, detect_pay, evaluate_job,
    location_fit,
)

CFG = load_search_config()
POLICY = CFG.location_policy  # on-site only Mumbai / Navi Mumbai / Thane; remote preferred


def job(title, location="", desc=""):
    return {"title": title, "location": location, "description_text": desc}


# ── Fixture postings ───────────────────────────────────────────────────────

def test_sde_intern_bengaluru_is_accepted_but_outside_policy():
    r = evaluate_job(job("SDE Intern - Bengaluru", "Bengaluru, Karnataka, India",
                         "Stipend: ₹25,000/month. You will build backend services."), CFG)
    assert r["classification_status"] == "AUTO_ACCEPT"
    assert r["classification_category"] == "INTERNSHIP"
    assert r["location_fit"] == "outside_policy"   # flagged, not rejected
    assert r["work_mode"] == "onsite"
    assert r["reject_reason"] is None
    assert (r["pay_status"], r["stipend_min"], r["stipend_currency"], r["stipend_period"]) == ("PAID", 25000, "INR", "month")


def test_wfh_data_science_intern_with_rupee_stipend_in_title():
    r = evaluate_job(job("Data Science Intern (Work From Home) ₹15,000/month"), CFG)
    assert r["classification_status"] == "AUTO_ACCEPT"
    assert (r["work_mode"], r["location_fit"]) == ("remote", "ok")
    assert (r["pay_status"], r["stipend_min"], r["stipend_currency"]) == ("PAID", 15000, "INR")


def test_summer_intern_2027_matches_on_description():
    r = evaluate_job(job("Summer Intern 2027", "Remote",
                         "Join our software engineering team for summer 2027."), CFG)
    assert r["classification_status"] == "AUTO_ACCEPT"
    assert r["location_fit"] == "ok"


def test_ml_intern_mumbai_onsite_is_accepted_and_in_policy():
    # This was AUTO_REJECTed by the old hardcoded "Mumbai rule".
    r = evaluate_job(job("ML Intern – Mumbai (On-site)", "Mumbai, Maharashtra, India",
                         "Stipend: INR 20000 per month"), CFG)
    assert r["classification_status"] == "AUTO_ACCEPT"
    assert r["reject_reason"] is None
    assert (r["location_fit"], r["work_mode"]) == ("ok", "onsite")
    assert (r["stipend_min"], r["stipend_currency"], r["stipend_period"]) == (20000, "INR", "month")


def test_senior_role_is_rejected():
    r = evaluate_job(job("Senior Software Engineer", "Remote", "5+ years of experience required."), CFG)
    assert r["classification_status"] == "AUTO_REJECT"
    assert "Seniority" in r["reject_reason"]


def test_phd_only_internship_is_rejected():
    r = evaluate_job(job("Research Intern", "Remote",
                         "Machine learning research internship. Open to PhD students only."), CFG)
    assert r["classification_status"] == "AUTO_REJECT"
    assert "PhD" in r["reject_reason"]


def test_phd_allowed_alongside_other_degrees_is_not_rejected():
    r = evaluate_job(job("Research Intern", "Remote",
                         "Machine learning research. Pursuing a PhD, master's or bachelor's degree."), CFG)
    assert "PhD" not in (r["reject_reason"] or "")


def test_unpaid_internship_is_flagged_not_rejected():
    r = evaluate_job(job("Web Developer Intern", "Remote", "This is an unpaid internship."), CFG)
    assert r["pay_status"] == "UNPAID"
    assert r["classification_status"] == "AUTO_ACCEPT"


def test_non_technical_role_is_rejected():
    r = evaluate_job(job("Marketing Intern", "Remote"), CFG)
    assert r["classification_status"] == "AUTO_REJECT"


def test_cooperative_is_not_a_co_op_internship():
    # Real OpenAI posting that was AUTO_ACCEPTed because 'coop' matched 'Cooperative'.
    r = evaluate_job(job("Software Engineer, Cooperative AI", "San Francisco"), CFG)
    assert r["classification_status"] == "AUTO_REJECT"


def test_full_time_role_is_rejected():
    r = evaluate_job(job("Software Engineer", "Remote", "Full-time position."), CFG)
    assert "not explicitly an internship" in r["reject_reason"]


# ── Pay detection ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, status, amount, currency, period", [
    ("Stipend: ₹15,000/month", "PAID", 15000, "INR", "month"),
    ("₹ 10,000 - ₹ 20,000 /month", "PAID", 10000, "INR", "month"),
    ("Rs. 8,000 - 12,000 /month", "PAID", 8000, "INR", "month"),
    ("INR 10000 per month", "PAID", 10000, "INR", "month"),
    ("12000 INR per month", "PAID", 12000, "INR", "month"),
    ("Stipend 15k/month", "PAID", 15000, None, "month"),
    ("$25/hr", "PAID", 25, "USD", "hour"),
    ("A competitive stipend is offered.", "PAID", None, None, None),
    ("Stipend: Performance based", "UNKNOWN", None, None, None),
    ("This is an unpaid internship", "UNPAID", None, None, None),
    ("No stipend will be provided.", "UNPAID", None, None, None),
    ("401(k) matching and health insurance", "UNKNOWN", None, None, None),
    ("Our product serves 10k users daily", "UNKNOWN", None, None, None),
    ("", "UNKNOWN", None, None, None),
])
def test_detect_pay(text, status, amount, currency, period):
    r = detect_pay(text)
    assert (r["pay_status"], r["stipend_min"], r["stipend_currency"], r["stipend_period"]) == (status, amount, currency, period)


def test_detect_pay_range_keeps_max():
    assert detect_pay("₹ 10,000 - ₹ 20,000 /month")["stipend_max"] == 20000


def test_performance_based_with_fixed_amount_is_paid():
    assert detect_pay("₹5,000/month + performance based incentives")["pay_status"] == "PAID"


def test_performance_based_evidence_is_recorded():
    assert "Performance based" in detect_pay("Stipend: Performance based")["pay_evidence"]


# ── Location policy ────────────────────────────────────────────────────────

@pytest.mark.parametrize("location, title, fit, mode", [
    ("Mumbai, Maharashtra, India", "ML Intern", "ok", "onsite"),
    ("Navi Mumbai (Hybrid)", "SDE Intern", "ok", "hybrid"),
    ("Thane, India", "Data Intern", "ok", "onsite"),
    ("Bengaluru, Karnataka, India", "SDE Intern", "outside_policy", "onsite"),
    ("Pune (Hybrid)", "SDE Intern", "outside_policy", "hybrid"),
    ("Remote - India", "SDE Intern", "ok", "remote"),
    ("Work from home", "SDE Intern", "ok", "remote"),
    ("", "Backend Intern (Remote)", "ok", "remote"),
    ("", "Backend Intern", "unknown", "unknown"),
    ("Bengaluru / Mumbai", "SDE Intern", "ok", "onsite"),
    ("Borivali, Maharashtra, India", "Dotnet Core Developer Intern", "ok", "onsite"),  # real LinkedIn row
])
def test_location_fit(location, title, fit, mode):
    assert location_fit(location, title, POLICY) == (fit, mode)


def test_remote_disallowed_policy_flags_remote_jobs():
    policy = LocationPolicy(onsite_cities=["Mumbai"], remote="disallowed")
    assert location_fit("Remote", "SDE Intern", policy) == ("outside_policy", "remote")


def test_source_supplied_work_mode_wins():
    assert location_fit("Bengaluru", "SDE Intern", POLICY, work_mode="remote") == ("ok", "remote")


# ── Enrichment (Phase 1a) ──────────────────────────────────────────────────

@pytest.mark.parametrize("title, family", [
    ("SDE Intern", "swe"),
    ("Backend Developer Intern", "swe"),
    ("Full Stack Intern", "swe"),
    ("Android Intern", "swe"),
    ("Machine Learning Intern", "ml"),
    ("AI Engineer Intern", "ml"),
    ("GenAI Intern", "ml"),
    ("NLP Research Intern", "ml"),
    ("Data Science Intern", "ds"),
    ("Data Analyst Intern", "ds"),
    ("Data Engineer Intern", "data_eng"),
    ("Research Intern", "research"),
    ("Marketing Intern", "other"),
])
def test_classify_role_family(title, family):
    assert classify_role_family(title) == family


@pytest.mark.parametrize("text, months", [
    ("Duration: 6 Months", 6),
    ("This is a 3-month internship", 3),
    ("Internship duration 3 - 6 months", 3),
    ("2 months internship, starts immediately", 2),
    ("5+ years of experience; 24 months warranty", None),
    ("Paid time off every 3 months", None),
    ("", None),
])
def test_detect_duration_months(text, months):
    assert detect_duration_months(text) == months


@pytest.mark.parametrize("source, ats, url, channel", [
    ("career_pages", "greenhouse", "https://boards.greenhouse.io/x/jobs/1", "ats_direct"),
    ("linkedin", None, "https://jobs.lever.co/x/abc", "ats_direct"),
    ("linkedin", None, "https://www.linkedin.com/jobs/view/1", "unknown"),
    ("internshala", None, "https://internshala.com/internship/detail/x", "internshala"),
    ("remotive", None, "https://docs.google.com/forms/d/e/abc/viewform", "google_form"),
    ("remotive", None, "mailto:jobs@x.in", "email"),
    ("remoteok", None, "https://remoteok.com/remote-jobs/1", "unknown"),
])
def test_detect_apply_channel(source, ats, url, channel):
    assert detect_apply_channel(source, ats, url) == channel


def test_evaluate_job_returns_enrichment_fields():
    r = evaluate_job({"title": "ML Intern", "location": "Navi Mumbai (Hybrid)", "source": "career_pages",
                      "ats_platform": "lever", "application_url": "https://jobs.lever.co/x/1",
                      "description_text": "Duration: 6 months. Stipend ₹30,000/month."}, CFG)
    assert (r["city"], r["country"], r["work_mode"], r["location_fit"]) == ("Navi Mumbai", "India", "hybrid", "ok")
    assert (r["role_family"], r["duration_months"], r["apply_channel"]) == ("ml", 6, "ats_direct")
    assert (r["stipend_min"], r["stipend_period"]) == (30000, "month")


def test_policy_city_aliases_are_normalized():
    policy = LocationPolicy(onsite_cities=["Bombay"], remote="preferred")
    assert location_fit("Mumbai, Maharashtra", "SDE Intern", policy) == ("ok", "onsite")
