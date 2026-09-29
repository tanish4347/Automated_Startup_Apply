"""Question harvest: form parsers (fixtures recorded from real forms 2026-09-30), storage by
distinct company, clustering and its unsure flags, source-mix weighting, the report, and the
read-only browser guard that makes submitting impossible."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401  (registers all tables)
from autoapply.ats.http import AsyncFetcher
from autoapply.models.base import Base
from autoapply.models.form_question import FormQuestion
from autoapply.questions.cluster import cluster_rows, platform_weights, render, rule_key, weighted_volume
from autoapply.questions.harvest import (
    HarvestedField, HarvestedForm, harvest_ats, normalize_label, parse_ashby, parse_greenhouse, parse_lever_form,
    pick_jobs, store_forms,
)
from autoapply.questions.platform_forms import FORM_DUMP_JS, fields_from_dump, parse_smartrecruiters_config
from autoapply.services.job_service import ingest_job

FIX = Path(__file__).parent / "fixtures" / "questions"


def load_json(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# ── parsers ──────────────────────────────────────────────────────────────────

def test_greenhouse_questions_true():
    fields = parse_greenhouse(load_json("greenhouse_observeai.json"))
    labels = [f.label for f in fields]
    assert labels[:5] == ["First Name", "Last Name", "Preferred First Name", "Email", "Phone"]
    assert "Latitude" not in labels and "Longitude" not in labels  # autocomplete plumbing, not questions
    resume = next(f for f in fields if f.label == "Resume/CV")
    assert (resume.field_type, resume.required) == ("file", False)
    assert next(f for f in fields if f.label == "Location").section == "location"


def test_ashby_graphql_form():
    fields = parse_ashby(load_json("ashby_openai.json"))
    by = {f.label: f for f in fields}
    assert by["Legal Name"].section == "system" and by["Legal Name"].required
    assert by["When can you start a new role?"].field_type == "date"
    assert by["Additional Information"].field_type == "textarea" and not by["Additional Information"].required
    certify = next(f for f in fields if f.label.startswith("I hereby certify"))
    assert certify.field_type == "multi_select" and certify.options == ["I confirm I have read the above."]


def test_lever_apply_page_form():
    fields = parse_lever_form((FIX / "lever_cred_apply.html").read_text())
    by = {f.label: f for f in fields}
    assert by["Resume/CV"].field_type == "file" and by["Resume/CV"].required
    assert by["Email"].field_type == "email" and by["Full name"].required
    assert not by["LinkedIn URL"].required and "✱" not in "".join(by)


def test_smartrecruiters_config():
    fields = parse_smartrecruiters_config(load_json("smartrecruiters_config.json"))
    by = {f.label: f for f in fields}
    assert by["Resume"].field_type == "file" and by["Resume"].required
    assert by["LinkedIn profile"].required is False
    assert "Facebook profile" not in by  # visible: false


def test_pick_jobs_prefers_internships():
    assert pick_jobs([("1", "Senior SRE"), ("2", "ML Intern"), ("3", "Staff PM")], 2) == ["2", "1"]


def test_harvest_ats_one_entry_per_company():
    gh = json.dumps(load_json("greenhouse_observeai.json"))

    def handler(request):
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": [{"id": 1, "title": "Intern"}, {"id": 2, "title": "SDE"},
                                                      {"id": 3, "title": "PM"}]})
        return httpx.Response(200, text=gh, headers={"content-type": "application/json"})

    async def go():
        async with AsyncFetcher(max_retries=0, transport=httpx.MockTransport(handler)) as f:
            return await harvest_ats("greenhouse", [("observeai", "Observe.AI")], fetcher=f)

    forms = asyncio.run(go())
    assert len(forms) == 2 and {f.company_key for f in forms} == {"observeai"}  # 2 postings, one company


# ── storage ──────────────────────────────────────────────────────────────────

def _form(platform, company, *labels, required=True, ftype="text"):
    return HarvestedForm(platform, company, company.title(), f"https://x/{company}",
                         [HarvestedField(l, ftype, required) for l in labels])


def test_store_forms_counts_postings_and_distinct_companies(session):
    store_forms(session, [_form("greenhouse", "a", "Email", "Phone"), _form("greenhouse", "a", "Email"),
                          _form("greenhouse", "b", "EMAIL"), _form("lever", "c", "Email")])
    rows = {(r.platform, r.company_key, r.raw_label): r for r in session.query(FormQuestion)}
    assert rows[("greenhouse", "a", "Email")].times_seen == 2          # 2 postings, 1 company
    assert rows[("greenhouse", "a", "Email")].distinct_companies == 2  # a and b: "EMAIL" normalises to "email"
    assert rows[("lever", "c", "Email")].distinct_companies == 1


# ── clustering ───────────────────────────────────────────────────────────────

def test_rule_keys_split_work_authorization_by_country():
    assert rule_key(normalize_label("Are you legally authorized to work in the United States?"))[0] == "work_authorization.us"
    assert rule_key(normalize_label("Are you authorised to work in India?"))[0] == "work_authorization.india"
    assert rule_key(normalize_label("Are you authorized to work in the country where the job is located?"))[0] == "work_authorization"
    assert rule_key(normalize_label("Will you now or in the future require visa sponsorship?"))[0] == "visa_sponsorship"
    assert rule_key(normalize_label("LinkedIn Profile"))[0] == "linkedin_url"
    assert rule_key(normalize_label("Preferred First Name"))[0] == "preferred_name"
    assert rule_key(normalize_label("First Name"))[0] == "first_name"
    assert rule_key(normalize_label("Why do you want to join Razorpay?")) == ("why_company", False)
    assert rule_key(normalize_label("Describe your favourite data structure")) is None
    # long labels that merely mention a profile field are not that field
    assert rule_key(normalize_label("What's one thing about you we won't see on your resume?")) is None
    assert rule_key(normalize_label("A customer emails because their payment failed. Write the email reply you'd send.")) is None
    assert rule_key(normalize_label("Will you now or in the future require sponsorship for employment visa status (e.g. H-1B)?"))[0] == "visa_sponsorship"


def _rows(*specs):
    return [FormQuestion(platform=p, company_key=c, raw_label=l, field_type=t, is_required=r)
            for p, c, l, t, r in specs]


def test_unsure_flags():
    rows = _rows(
        ("greenhouse", "a", "What is your expected stipend per month for this internship", "text", True),
        ("greenhouse", "b", "What is your expected monthly stipend for this internship", "text", True),
        ("lever", "c", "How did you hear about us?", "select", False),
        ("ashby", "d", "Source", "text", False),
        ("greenhouse", "e", "Current city", "text", True),
        ("internshala", "internshala", "Describe a project you are proud of", "textarea", True),
    )
    clusters = {c.key: c for c in cluster_rows(rows)}
    assert clusters["salary_expectation"].rows and not clusters["salary_expectation"].reasons
    how = clusters["how_heard"]
    assert any("broad rule" in r for r in how.reasons) and any("answer kinds differ" in r for r in how.reasons)
    project = clusters["custom.describe_a_project_you_are_proud"]
    assert project.how == "exact" and not project.reasons


def test_similarity_merge_is_flagged():
    rows = _rows(("greenhouse", "a", "Describe a hard technical problem you solved recently", "textarea", True),
                 ("lever", "b", "Describe a hard technical problem you solved", "textarea", False))
    (c,) = cluster_rows(rows)
    assert c.how == "similarity" and c.reasons and len(c.companies()) == 2


# ── weighting and the report ─────────────────────────────────────────────────

def test_platform_weights_follow_the_real_source_mix(session):
    for i in range(6):
        ingest_job(session, {"title": f"Intern {i}", "company": f"C{i}", "source": "internshala",
                             "classification_status": "AUTO_ACCEPT"})
    for i in range(3):
        ingest_job(session, {"title": f"SDE Intern {i}", "company": f"G{i}", "source": "ats_boards",
                             "classification_status": "AUTO_ACCEPT",
                             "application_url": f"https://boards.greenhouse.io/g{i}/jobs/{i}"})
    ingest_job(session, {"title": "ML Intern", "company": "L", "source": "linkedin", "classification_status": "AUTO_ACCEPT"})
    weights, counts = platform_weights(session)
    assert counts["internshala"] == 6 and counts["greenhouse"] == 3 and "linkedin" not in counts
    assert weights["internshala"] == pytest.approx(6 / 9)


def test_render_ranks_by_weight_and_draws_the_stopping_line():
    rows = _rows(("internshala", "internshala", "Cover letter", "textarea", True),
                 *[("greenhouse", f"c{i}", "Email", "text", True) for i in range(50)],
                 ("greenhouse", "c0", "Describe your favourite data structure", "textarea", False))
    clusters = cluster_rows(rows)
    weights, sampled = {"internshala": 0.6, "greenhouse": 0.4}, {"internshala": 1, "greenhouse": 50}
    by = {c.key: c for c in clusters}
    assert weighted_volume(by["cover_letter"], weights, sampled) == pytest.approx(0.6)
    assert weighted_volume(by["email"], weights, sampled) == pytest.approx(0.4)
    md = render(clusters, weights, {"internshala": 600, "greenhouse": 400}, sampled)
    assert md.index("`cover_letter`") < md.index("`email`") < md.index("stopping line") < md.index("favourite_data")
    assert "Clusters (3)" in md


# ── platform forms: the read-only guard ─────────────────────────────────────

def test_fields_from_dump():
    dump = {"fields": [{"label": "Cover letter *", "field_type": "textarea", "required": True},
                       {"label": "", "field_type": "text"},
                       {"label": "Are you available for 3 months?", "field_type": "select", "required": True,
                        "options": ["Yes", "No", ""]}]}
    fields = fields_from_dump(dump)
    assert [(f.label, f.field_type, f.required, f.options) for f in fields] == [
        ("Cover letter", "textarea", True, None), ("Are you available for 3 months?", "select", True, ["Yes", "No"])]


PAGE = """<html><body>
<form id="f" method="post" action="/submit-application">
  <label for="cl">Cover letter *</label><textarea id="cl" required></textarea>
  <div><p>Gender</p><div class="opts">
    <label><input type="radio" name="gender" value="f"> Female</label>
    <label><input type="radio" name="gender" value="m"> Male</label>
    <label><input type="radio" name="gender" value="x"> Prefer not to say</label></div></div>
  <fieldset><legend>Are you available for 3 months?</legend>
    <label><input type="radio" name="avail" value="y" required> Yes</label>
    <label><input type="radio" name="avail" value="n"> No</label></fieldset>
  <label for="cv">Upload resume</label><input type="file" id="cv">
  <input type="hidden" name="csrf" value="x">
  <button id="apply" type="submit">Submit application</button>
</form>
<script>
  window.sent = [];
  document.getElementById('apply').addEventListener('click', () =>
    fetch('/api/apply', {method: 'POST', body: 'x'}).then(() => window.sent.push('ok'), () => window.sent.push('blocked')));
</script></body></html>"""


def test_read_only_mode_blocks_submission_and_dumps_the_form(tmp_path):
    pytest.importorskip("playwright")
    from autoapply.sources.browser import BrowserSession
    try:
        s = BrowserSession("formtest", profile_root=tmp_path, record_calls=False).__enter__()
    except Exception as e:  # no browser installed on this machine
        pytest.skip(f"chromium not available: {e}")
    try:
        s.read_only()
        s._ctx.route("https://form.test/", lambda r: r.fulfill(status=200, body=PAGE, content_type="text/html")) \
            if False else None
        s.page.route("https://form.test/**", lambda r: r.fulfill(status=200, body=PAGE, content_type="text/html")
                     if r.request.method == "GET" else r.fallback())
        s.page.goto("https://form.test/job/1")
        dump = s.page.evaluate(FORM_DUMP_JS)
        s.page.click("#apply")
        s.page.wait_for_timeout(500)
        sent = s.page.evaluate("window.sent")
    finally:
        s.__exit__(None, None, None)
    labels = {f["label"]: f for f in dump["fields"]}
    assert labels["Cover letter *"]["required"] and labels["Cover letter *"]["field_type"] == "textarea"
    radio = labels["Are you available for 3 months?"]
    assert radio["field_type"] == "select" and radio["options"] == ["Yes", "No"] and radio["required"]
    assert labels["Upload resume"]["field_type"] == "file"
    # a radio group without a fieldset is still one question, labelled by the text before the group
    assert labels["Gender"]["options"] == ["Female", "Male", "Prefer not to say"]
    assert "Female" not in labels and "Male" not in labels
    assert "csrf" not in json.dumps(dump)
    assert sent == ["blocked"]                                    # the fetch POST never left
    assert any(b.startswith("POST") for b in s.blocked)           # and the form's own submit POST too
