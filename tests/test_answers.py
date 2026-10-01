"""Answer engine tiers 1-2: alias / rule / embedding identification, verbatim vault resolution,
SENSITIVE park-or-verbatim, constrained entailment validated against the form's options, the
guarantee that no SENSITIVE value reaches a prompt, write-back, and explain."""

import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401
from autoapply.answers import engine as E
from autoapply.answers.entail import Entailer, SensitiveLeak, build_prompt, prompt_rules, sensitive_values
from autoapply.candidate import intake
from autoapply.candidate.catalog import seed_catalog
from autoapply.candidate.sensitive import ParkApplication, user_input
from autoapply.models.answer_alias import QuestionAlias
from autoapply.models.base import Base
from autoapply.models.vault import VaultAnswer, VaultPolicy

CFG = {"similarity_floor": 0.86, "sensitive_similarity_floor": 0.93}


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    seed_catalog(s)
    E.seed_aliases(s)
    return s


class FakeIndex:
    """Stands in for the embedding index: returns a preset neighbour."""
    def __init__(self, key=None, text="", sim=0.0):
        self.hit = (key, text, sim) if key else None

    def build(self, session):
        pass

    def nearest(self, label):
        return self.hit


@pytest.fixture(autouse=True)
def no_real_embeddings(monkeypatch):
    """Tests never load the embedding model (or download it)."""
    monkeypatch.setattr(E, "EmbeddingIndex", lambda model_name: FakeIndex())


class FakeEntailer:
    def __init__(self, answer=None, up=True):
        self.answer, self.up, self.prompts = answer, up, []

    def available(self):
        return self.up

    def ask(self, prompt, options):
        self.prompts.append(prompt)
        return self.answer if self.answer in options else None


def confirm(session, key, value):
    a = session.query(VaultAnswer).filter_by(canonical_key=key).one()
    intake.apply(session, f"answer:{a.id}", "confirm", {"value": value})


def eng(session, index=None, entailer=None, writeback=True):
    return E.AnswerEngine(session, CFG, entailer=entailer or FakeEntailer(up=False), index=index or FakeIndex(),
                          writeback=writeback)


# ── tier 1 ───────────────────────────────────────────────────────────────────

def test_aliases_seeded_from_the_report(session):
    assert session.query(QuestionAlias).count() > 50
    assert session.query(QuestionAlias).filter_by(alias="linkedin profile").one().canonical_key == "linkedin_url"


def test_tier_1a_alias_and_rule(session):
    e = eng(session)
    assert (e.identify("LinkedIn Profile").tier, e.identify("LinkedIn Profile").canonical_key) == ("1a", "linkedin_url")
    r = e.identify("Your personal e-mail address please")
    assert (r.tier, r.canonical_key) == ("1a-rule", "email")


def test_tier_1b_floor_and_country_guard(session):
    assert eng(session, FakeIndex("current_company", "current employer", 0.90)).identify("Where do you work now?").tier == "1b"
    assert eng(session, FakeIndex("current_company", "current employer", 0.80)).identify("Where do you work now?").tier is None
    # SENSITIVE keys need the stricter floor
    assert eng(session, FakeIndex("gpa", "cgpa", 0.90)).identify("Academic standing score").canonical_key is None
    assert eng(session, FakeIndex("gpa", "cgpa", 0.95)).identify("Academic standing score").canonical_key == "gpa"
    # a label naming another country never maps to a country-bound key
    assert eng(session, FakeIndex("work_authorization.india", "authorized to work in india", 0.97)).identify(
        "Do you hold the right to labour in Germany?").canonical_key is None


def test_tier_1c_confirmed_verbatim_needs_review_ignored(session):
    e = eng(session)
    row = session.query(VaultAnswer).filter_by(canonical_key="github_url").one()
    row.answer, row.status, row.source = "https://github.com/asha", "NEEDS REVIEW", "CV"
    session.commit()
    assert e.resolve("GitHub profile", "text").value is None            # NEEDS REVIEW = missing
    confirm(session, "github_url", "https://github.com/asha")
    r = e.resolve("GitHub profile", "text")
    assert (r.value, r.tier, r.canonical_key) == ("https://github.com/asha", "1a", "github_url")


def test_stored_answer_is_mapped_onto_the_forms_options(session):
    confirm(session, "relocation", "No")
    r = eng(session).resolve("Are you open to relocation?", "select", ["Select...", "Yes, anywhere", "No, I can't relocate"])
    assert r.value == "No, I can't relocate"
    assert E.match_option("no", ["Yes", "No"]) == "No" and E.match_option("Maybe", ["Yes", "No"]) is None


# ── SENSITIVE: tier 1 or park, never a model ────────────────────────────────

def test_sensitive_parks_when_missing_and_is_verbatim_when_confirmed(session):
    ent = FakeEntailer("Yes")
    e = eng(session, entailer=ent)
    with pytest.raises(ParkApplication):
        e.resolve("Will you now or in the future require visa sponsorship?", "select", ["Yes", "No"])
    assert ent.prompts == []                                   # never sent to the model
    confirm(session, "visa_sponsorship", "No")
    assert e.resolve("Will you now or in the future require visa sponsorship?", "select", ["Yes", "No"]).value == "No"
    assert ent.prompts == []


def test_no_sensitive_value_can_reach_a_prompt(session):
    confirm(session, "salary_expectation", "INR 15000 per month")
    confirm(session, "gpa", "7.78/10")
    with user_input():
        session.add_all([VaultPolicy(key="can_work_onsite_in", value={"cities": ["Mumbai", "Navi Mumbai", "Thane"]}),
                         VaultPolicy(key="will_relocate", value={"value": False}),
                         VaultPolicy(key="stipend_floor", value={"amount": 15000, "currency": "INR"}),
                         VaultPolicy(key="notice_period", value={"days": 30}),
                         VaultPolicy(key="availability_window", value={"start": "2026-12-01"})])
        session.commit()
    forbidden = sensitive_values(session)
    assert {"INR 15000 per month", "7.78/10", "15000", "2026-12-01"} <= forbidden
    rules = prompt_rules(session)
    assert set(rules) == {"can_work_onsite_in", "will_relocate"}   # sensitive policy rows never included
    ent = FakeEntailer("No")
    r = eng(session, entailer=ent).resolve("Can you work from our Bangalore office?", "select", ["Yes", "No"])
    assert r.value == "No" and r.tier == "2"
    for prompt in ent.prompts:
        assert not any(v.lower() in prompt.lower() for v in forbidden)
        assert "Mumbai" in prompt                                   # the rule it decided from
    with pytest.raises(SensitiveLeak):                              # even a question carrying one is refused
        build_prompt("Is 7.78/10 your CGPA?", ["Yes", "No"], rules, forbidden)


# ── tier 2 ───────────────────────────────────────────────────────────────────

def _with_rules(session):
    with user_input():
        session.add(VaultPolicy(key="can_work_onsite_in", value={"cities": ["Mumbai"]}))
        session.commit()


def test_tier2_answer_must_be_one_of_the_options(session):
    _with_rules(session)
    r = eng(session, entailer=FakeEntailer("Probably")).resolve("Can you work from Pune?", "select", ["Yes", "No"])
    assert r.value is None and r.park_reason


def test_tier2_only_for_finite_answer_spaces(session):
    _with_rules(session)
    ent = FakeEntailer("Yes")
    r = eng(session, entailer=ent).resolve("Describe your ideal team culture", "textarea")
    assert r.value is None and "free text" in r.park_reason and ent.prompts == []


def test_tier2_skips_cleanly_when_ollama_is_down(session, caplog):
    _with_rules(session)
    down = Entailer(transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused"))))
    assert down.available() is False
    r = eng(session, entailer=down).resolve("Can you work from Pune?", "select", ["Yes", "No"])
    assert r.value is None


def test_entailer_validates_against_options():
    def handler(req):
        body = json.loads(req.content)
        assert body["format"]["properties"]["answer"]["enum"] == ["Yes", "No", None]
        return httpx.Response(200, json={"message": {"content": '{"answer": "Absolutely"}'}})
    ent = Entailer(transport=httpx.MockTransport(handler))
    assert ent.ask("prompt", ["Yes", "No"]) is None


# ── write-back and instrumentation ───────────────────────────────────────────

def test_writeback_alias_new_question_and_review_correction(session):
    confirm(session, "current_company", "Example Payments")
    e = eng(session, FakeIndex("current_company", "current employer", 0.91))
    assert e.resolve("Where do you work now?", "text").value == "Example Payments"
    assert session.query(QuestionAlias).filter_by(alias="where do you work now").one().source == "embedding"
    assert eng(session).identify("Where do you work now?").tier == "1a"       # next time: tier 1a

    e2 = eng(session)
    e2.resolve("Which hackathons have you won?", "text")
    new = session.query(VaultAnswer).filter_by(canonical_key="custom.which_hackathons_have_you_won").one()
    assert (new.status, new.answer, new.source) == ("NEEDS REVIEW", None, "FORM")

    key = E.record_correction(session, "Which hackathons have you won?", "Smart India Hackathon 2025")
    row = session.query(VaultAnswer).filter_by(canonical_key=key).one()
    assert row.suggested_answer == "Smart India Hackathon 2025" and row.status == "NEEDS REVIEW"   # never auto-confirmed
    assert e.stats["tier 1b"] == 1 and e.stats["writeback_alias"] == 1 and e2.stats["parked"] == 1


def test_embedding_never_writes_back_a_sensitive_alias(session):
    confirm(session, "gpa", "7.78/10")
    e = eng(session, FakeIndex("gpa", "cgpa", 0.96))
    assert e.resolve("Academic standing score", "text").value == "7.78/10"
    assert session.query(QuestionAlias).filter_by(alias="academic standing score").first() is None


def test_explain_fills_and_writes_nothing(session):
    confirm(session, "email", "asha@example.edu")
    before = session.query(QuestionAlias).count(), session.query(VaultAnswer).count()
    rows = E.explain(session, [("Email", "email", None), ("A brand new question?", "text", None),
                               ("Do you require visa sponsorship?", "select", ["Yes", "No"])])
    assert [r["tier"] for r in rows] == ["1a", "parked", "parked"]
    assert rows[0]["value"] == "asha@example.edu" and "SENSITIVE" in rows[2]["reason"]
    assert (session.query(QuestionAlias).count(), session.query(VaultAnswer).count()) == before
