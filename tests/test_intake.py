"""Intake: CV parser, catalog seeding from docs/QUESTIONS.md, the SENSITIVE write guard, the
answer engine's verbatim-or-park rule, the intake passes, and the /intake UI endpoints.
The CV fixture is invented (tests/fixtures/candidate/sample_cv.txt); no real CV is used."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401  (registers all tables and the SENSITIVE guard)
from autoapply.candidate import intake
from autoapply.candidate.catalog import parse_report, seed_catalog
from autoapply.candidate.cv_parser import import_cv, parse_file
from autoapply.candidate.sensitive import (
    SENSITIVE, SENSITIVE_KEYS, ParkApplication, SensitiveWriteError, is_sensitive_key, sensitive_answer, user_input,
)
from autoapply.models.base import Base
from autoapply.models.vault import VaultAnswer, VaultEducation, VaultIdentity, VaultPolicy

CV = Path(__file__).parent / "fixtures" / "candidate" / "sample_cv.txt"


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture
def seeded(session):
    seed_catalog(session)
    return session


# ── CV parser ────────────────────────────────────────────────────────────────

def test_cv_parser():
    cv = parse_file(CV)
    assert (cv.name, cv.email, cv.phone) == ("Asha Rao", "asha.rao@example.edu", "+91-9000000001")
    assert cv.links == {"linkedin": "https://linkedin.com/in/asha-rao", "github": "https://github.com/asharao"}
    iit, school = cv.education
    assert (iit.institution, iit.degree, iit.major, iit.minor) == (
        "Indian Institute of Technology, Bombay", "B.Tech.", "Computer Science and Engineering", "Economics")
    assert (iit.score, iit.scale, iit.start_date, iit.end_date) == ("8.41", "10", "2023", "2027")
    assert (school.institution, school.degree, school.major, school.score) == ("Delhi Public School", "XII(CBSE)", None, "94.2")
    swe, research = cv.experience
    assert (swe.title, swe.company, swe.area, len(swe.bullets)) == ("Software Engineering Intern", "Example Payments Pvt. Ltd.", "Backend", 2)
    assert research.is_current and research.company == "Department of Computer Science, IIT Bombay"
    (proj,) = cv.projects
    assert (proj.name, proj.technologies, proj.start_date) == ("Tiny Search Engine", "Rust, Tantivy", "Aug 2025")
    assert cv.skills == {"Languages": ["Python", "Rust", "SQL"], "ML": ["PyTorch", "Transformers", "RAG"]}


def test_import_cv_writes_needs_review_and_only_suggests_sensitive(seeded):
    counts = import_cv(seeded, CV)
    assert counts["education"] == 2 and counts["experience"] == 2 and counts["projects"] == 1 and counts["skills"] == 2
    ident = seeded.query(VaultIdentity).one()
    assert (ident.first_name, ident.email, ident.source, ident.status) == ("Asha", "asha.rao@example.edu", "CV", "NEEDS REVIEW")
    assert all(e.source == "CV" and e.status == "NEEDS REVIEW" for e in ident.educations)
    assert all(e.cgpa is None and e.end_date is None for e in ident.educations)   # SENSITIVE: never from the CV
    ans = {a.canonical_key: a for a in seeded.query(VaultAnswer)}
    assert (ans["email"].answer, ans["email"].source, ans["email"].status) == ("asha.rao@example.edu", "CV", "NEEDS REVIEW")
    assert ans["gpa"].answer is None and ans["gpa"].suggested_answer == "8.41/10"
    assert ans["graduation_date"].answer is None and ans["graduation_date"].suggested_answer == "2027"
    assert import_cv(seeded, CV)["education"] == 0  # idempotent


# ── catalog ──────────────────────────────────────────────────────────────────

def test_catalog_comes_only_from_the_report(seeded):
    report_keys = {e.key for e in parse_report()}
    rows = seeded.query(VaultAnswer).all()
    assert rows and {r.canonical_key for r in rows} <= report_keys
    assert all(r.answer is None and r.status == "NEEDS REVIEW" and r.category and r.question for r in rows)
    for r in rows:
        assert (r.sensitivity == SENSITIVE) == is_sensitive_key(r.canonical_key), r.canonical_key
    assert seed_catalog(seeded)["seeded"] == 0  # idempotent


# ── the SENSITIVE guard: anything but the candidate's own input fails ────────

SENSITIVE_IN_CATALOG = sorted(e.key for e in parse_report() if is_sensitive_key(e.key))


def test_sensitive_set_covers_the_attestations():
    for base in ("work_authorization", "visa_sponsorship", "criminal_history", "gender", "race_ethnicity",
                 "veteran_status", "disability_status", "gpa", "graduation_date", "salary_expectation",
                 "notice_period", "start_date"):
        assert base in SENSITIVE_KEYS
    assert is_sensitive_key("work_authorization.us") and is_sensitive_key("custom.have_you_ever_been_convicted_of")
    assert SENSITIVE_IN_CATALOG  # the harvested catalog does contain attestations


@pytest.mark.parametrize("key", SENSITIVE_IN_CATALOG)
def test_sensitive_answer_rejects_non_user_writes(seeded, key):
    row = seeded.query(VaultAnswer).filter_by(canonical_key=key).one()
    row.answer = "Yes"                         # e.g. the CV parser, the answer engine, an LLM
    with pytest.raises(SensitiveWriteError):
        seeded.flush()
    seeded.rollback()
    row = seeded.query(VaultAnswer).filter_by(canonical_key=key).one()
    row.status = "CONFIRMED"                   # confirming it on the candidate's behalf
    with pytest.raises(SensitiveWriteError):
        seeded.flush()
    seeded.rollback()
    row = seeded.query(VaultAnswer).filter_by(canonical_key=key).one()
    row.sensitivity = "NORMAL"                 # downgrading it to get around the rule
    with pytest.raises(SensitiveWriteError):
        seeded.flush()
    seeded.rollback()
    with user_input():                         # the candidate's own input
        row = seeded.query(VaultAnswer).filter_by(canonical_key=key).one()
        row.answer, row.status, row.source = "Yes", "CONFIRMED", "USER ENTERED"
        seeded.commit()
    assert seeded.query(VaultAnswer).filter_by(canonical_key=key).one().answer == "Yes"


def test_sensitive_education_and_policy_rows_reject_non_user_writes(seeded):
    ident = VaultIdentity(first_name="A")
    seeded.add(ident)
    seeded.flush()
    for col in ("cgpa", "scale", "end_date"):
        seeded.add(VaultEducation(identity=ident, institution="X", **{col: "9"}))
        with pytest.raises(SensitiveWriteError):
            seeded.flush()
        seeded.rollback()
    seeded.add(VaultPolicy(key="stipend_floor", value={"amount": 1}))
    with pytest.raises(SensitiveWriteError):
        seeded.flush()
    seeded.rollback()
    seeded.add(VaultEducation(identity=VaultIdentity(), institution="X", degree="B.Tech"))  # non-sensitive: fine
    seeded.commit()


def test_cv_import_never_writes_a_sensitive_answer(seeded):
    import_cv(seeded, CV)
    for a in seeded.query(VaultAnswer).filter_by(sensitivity=SENSITIVE):
        assert a.answer is None and a.status != "CONFIRMED", a.canonical_key


# ── answer engine: verbatim or park, never generated ─────────────────────────

def test_answer_engine_returns_sensitive_verbatim_or_parks(seeded, monkeypatch):
    from autoapply.appliers import question_engine
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-be-used")
    import_cv(seeded, CV)
    ident = seeded.query(VaultIdentity).one()

    def llm_called(*a, **kw):
        raise AssertionError("a SENSITIVE question reached the model")
    monkeypatch.setattr("google.genai.Client", llm_called, raising=False)

    with pytest.raises(ParkApplication):   # nothing entered yet
        question_engine.answer_custom_question("Will you now or in the future require visa sponsorship?", "select",
                                               ["Yes", "No"], ident.to_profile_dict(), "")
    intake.apply(seeded, f"answer:{seeded.query(VaultAnswer).filter_by(canonical_key='visa_sponsorship').one().id}",
                 "confirm", {"value": "No"})
    seeded.refresh(ident)
    profile = ident.to_profile_dict()
    assert question_engine.answer_custom_question("Will you now or in the future require visa sponsorship?",
                                                  "select", ["Yes", "No"], profile, "") == "No"
    # the model-visible part of the profile holds no sensitive answer, grade or graduation date
    visible = {k: v for k, v in profile.items() if k != "sensitive"}
    assert "No" not in [a["answer"] for a in visible["answers"]]
    assert all("cgpa" not in e and "end_date" not in e for e in visible["education"])
    assert sensitive_answer(seeded, "visa_sponsorship") == "No"


def test_park_is_not_swallowed_by_generic_handlers():
    with pytest.raises(ParkApplication):
        try:
            raise ParkApplication("gpa")
        except Exception:   # what the appliers do around their whole flow
            pytest.fail("ParkApplication was caught by `except Exception`")


# ── intake passes ────────────────────────────────────────────────────────────

def test_intake_passes_order_resume_and_progress(seeded):
    import_cv(seeded, CV)
    cards = intake.build_cards(seeded)
    passes = [c.intake_pass for c in cards]
    assert passes == sorted(passes, key=intake.PASSES.index)          # a, b, c, d in order
    assert {"cv", "gap", "policy", "story"} <= set(passes)
    assert not any(c.intake_pass != "policy" and c.id.startswith("answer:") and
                   seeded.get(VaultAnswer, int(c.id[7:])).canonical_key in ("notice_period", "salary_expectation")
                   for c in cards)                                      # asked as policy, not twice
    p = intake.progress(cards)
    assert p["done"] == 0 and p["resume_at"] == 0 and all(x["minutes_left"] > 0 for x in p["passes"])

    first, second = cards[0], cards[1]
    intake.apply(seeded, first.id, "confirm", {i["name"]: i["value"] for i in first.inputs})
    intake.apply(seeded, second.id, "skip")
    cards = intake.build_cards(seeded)
    p = intake.progress(cards)
    assert p["done"] == 1 and cards[0].status == "CONFIRMED" and cards[1].status == "SKIPPED"
    assert p["resume_at"] == 2   # resumes at the first card never answered, not at the skipped one


def test_sensitive_suggestion_needs_the_candidates_confirm(seeded):
    import_cv(seeded, CV)
    gpa = seeded.query(VaultAnswer).filter_by(canonical_key="gpa").one()
    intake.apply(seeded, f"answer:{gpa.id}", "confirm", {"value": ""})   # Enter on the empty box accepts the CV value
    seeded.refresh(gpa)
    assert (gpa.answer, gpa.source, gpa.status) == ("8.41/10", "USER ENTERED", "CONFIRMED")


def test_policy_block_is_structured_and_answers_its_catalog_keys(seeded):
    intake.apply(seeded, "policy:can_work_onsite_in", "confirm", {"cities": "Mumbai, Navi Mumbai, Thane"})
    intake.apply(seeded, "policy:will_relocate", "confirm", {"value": False})
    intake.apply(seeded, "policy:stipend_floor", "confirm", {"amount": "15000"})
    intake.apply(seeded, "policy:notice_period", "confirm", {"days": "0"})
    pol = {p.key: p.value for p in seeded.query(VaultPolicy)}
    assert pol["can_work_onsite_in"] == {"cities": ["Mumbai", "Navi Mumbai", "Thane"]}
    assert pol["will_relocate"] == {"value": False}
    assert pol["stipend_floor"] == {"amount": 15000, "currency": "INR", "period": "month"}
    salary = seeded.query(VaultAnswer).filter_by(canonical_key="salary_expectation").one()
    assert salary.status == "CONFIRMED" and '"amount": 15000' in salary.answer and salary.source == "USER ENTERED"
    cov = intake.coverage(seeded)
    assert "stipend_floor" in cov["policy_set"] and "salary_expectation" not in cov["blank_sensitive"]


def test_identity_answers_sync_to_the_identity_row(seeded):
    import_cv(seeded, CV)
    email = seeded.query(VaultAnswer).filter_by(canonical_key="email").one()
    intake.apply(seeded, f"answer:{email.id}", "confirm", {"value": "asha@new.example"})
    assert seeded.query(VaultIdentity).one().email == "asha@new.example"
    seeded.refresh(email)
    assert (email.source, email.status) == ("USER ENTERED", "CONFIRMED")


# ── the UI endpoints ─────────────────────────────────────────────────────────

def test_intake_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from autoapply.dashboard import app as dash
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        seed_catalog(s)
        import_cv(s, CV)
    monkeypatch.setattr(dash, "_init_db", lambda: None)
    monkeypatch.setattr(dash, "_SessionFactory", Session)
    client = TestClient(dash.create_app())
    assert client.get("/intake").status_code == 200
    st = client.get("/intake/api/state").json()
    assert st["progress"]["total"] == len(st["cards"]) > 0
    gpa = next(c for c in st["cards"] if c["question"] and "gpa" in c["id"] or c["inputs"][0].get("suggestion") == "8.41/10")
    r = client.post("/intake/api/answer", json={"card": gpa["id"], "action": "confirm", "values": {"value": "8.41/10"}})
    assert r.status_code == 200 and r.json()["progress"]["done"] == 1   # a SENSITIVE write, allowed: it's the candidate
    with Session() as s:
        assert s.query(VaultAnswer).filter_by(canonical_key="gpa").one().answer == "8.41/10"
