# Code Audit — 2026-09-29

Scope: every file under `autoapply/`, checked against `ARCHITECTURE.md`. Nothing in the
doc was taken on trust. Line references are to the code **before** this commit unless marked.

Status legend: **FIXED** (in this commit) · **OPEN** (reported only; out of scope for this phase).

---

## 1. Claims in ARCHITECTURE.md that are false or overstated

| # | Claim (section) | Reality |
|---|---|---|
| 1.1 | "Jobs are fetched concurrently using a ThreadPoolExecutor in `orchestrator.py`" (§4) | False. `run_discovery` loops over sources sequentially; no executor anywhere in the repo. |
| 1.2 | "HTTP requests employ exponential backoff in `orchestrator.py` and `http_client.py`" (§13) | Half true. Only `HttpClient` backs off. The largest source, `career_pages.py`, used bare `requests` with no retry and `except: pass` (ported to `HttpClient` in this commit). |
| 1.3 | Job sources are "Greenhouse, Lever, LinkedIn, RemoteOK, Remotive" with dedicated adapters (§2, §3) | `GreenhouseSource`, `LeverSource`, `AshbySource`, `IndeedRssSource` exist but are never instantiated. Greenhouse/Lever/Ashby/SmartRecruiters/Workable are only reached through `CareerPagesSource`, which *guesses* each company's board token. Arbeitnow is active but undocumented. |
| 1.4 | "Target Fields" and location rules come from config (§5) | `search_config.yaml` and `sources_config.yaml` are never read. Targets, exclusions, queries and locations are hardcoded in `sources/filter.py` and `sources/orchestrator.py`. `config.yaml` is empty. |
| 1.5 | "Cumulative scoring engine … instead of rigid booleans" (§17) | Overstated. Any single `reject_reason` forces `AUTO_REJECT` regardless of score (`filter.py:147`), so the score only separates ACCEPT from REVIEW. |
| 1.6 | Rejected jobs are kept "to allow auditing of the search rules" (§17) | Kept, but never re-evaluated: a deduplicated re-sighting only bumps `last_seen` (`job_service.py:31-34`), so changing a rule has no effect on anything already stored. |
| 1.7 | "Cross-source canonical hashing" dedup (§2, §3) | Doesn't work across sources for LinkedIn, Arbeitnow or Ashby: any non-numeric `source_id` longer than 8 chars is hashed as `company::source_id` (`dedup.py:48-52`), which can never match the same job from another source. See also bug 2.2. |
| 1.8 | Queue state machine includes `MANUAL_REQUIRED`; unknown questions "gracefully halt, mark MANUAL_REQUIRED, capture a screenshot, preserve the session" (§6, §18) | `MANUAL_REQUIRED` does not exist in `ApplicationStatus`. No code takes screenshots (`confirmation_screenshot` is never written). Failures go to `FAILED`. No session is preserved: every applier opens a fresh non-persistent browser. |
| 1.9 | "Supports crash recovery … stalled applications are safely rolled back" (§6, §13) | False. `appliers/orchestrator.py` imports `reset_stalled_applications`, which does not exist, so the apply engine cannot even be imported. **OPEN.** |
| 1.10 | Candidate Vault lives in `autoapply/candidate/vault.py` / `candidate_vault.json`, with FACTS, PREFERENCES/POLICIES, STORY BANK, REUSABLE ANSWERS (§3, §7) | Neither file exists. The Vault is `models/vault.py`: identity, education, employment, projects, skills, resumes, answers. There is no preferences/policies table and no story bank. Until this commit the appliers didn't read the Vault at all (see §3 of this report). |
| 1.11 | "Question Brain" in `autoapply/intelligence/` does semantic classification to canonical keys, via "LLM or embedding step" (§3, §8) | `intelligence/analyzer.py` was empty. There is no classification, no canonical-key mapping and no embeddings. `appliers/question_engine.py` sends the **whole profile** plus the question to Gemini and trusts the model's self-reported `confidence`. |
| 1.12 | LLM provider is OpenAI / Anthropic, configured in `.env` (§9, §18) | False. The only LLM call is Google Gemini (`gemini-2.5-flash`, `GEMINI_API_KEY`). `google-genai` isn't a declared dependency. `.env.example` lists OpenAI/Anthropic keys that nothing reads, and omits `GEMINI_API_KEY`. |
| 1.13 | LLM is used for "rewriting Story Bank bullets" and "Cover Letter intros" (§9) | No such code exists. |
| 1.14 | "Refuses to hallucinate and stops" when data is missing (§8, §9) | Overstated. The applier itself fabricates data: a single-word name becomes last name `"Applicant"`, and Lever's current-company field is filled with the candidate's location (bug 2.1). |
| 1.15 | "ATS Isolation: only the failing applier fails" (§13) | Only 4 appliers are registered (Greenhouse, Lever, Ashby, SmartRecruiters). `WorkableApplier` and `BreezyApplier` exist but are never registered, so those jobs always fail with "No suitable adapter". |
| 1.16 | Pydantic validates "API data" in `sources/` (§16) | Sources use a plain `@dataclass` (`SourceResult`). Pydantic is used only in `config.py` and for the Gemini response schema. |
| 1.17 | `jobs` stores "YoE, classification tracking" (§11) | There is no YoE column. YoE survives only as free text inside `classification_evidence`. |
| 1.18 | Key table `vault_entries` (§11) | Doesn't exist. The tables are `vault_identity`, `vault_education`, `vault_employment`, `vault_project`, `vault_skill`, `vault_resume`, `vault_answer`. |
| 1.19 | A `.vault_key` can encrypt sensitive DB fields (§12) | `security.py` is never imported. It hardcodes a Windows path (`c:\Users\Tanish Mutha\...`) and needs `cryptography`, which isn't a declared dependency. Nothing is encrypted. |
| 1.20 | Tests "verify ATS endpoints and database interactions" (§14) | `test_career.py` and `test_e2e.py` assert nothing. `test_e2e.py` writes a fake "E2E Mock Job" into the **real** `data/autoapply.db` and needs Playwright. The other five test files were empty. |
| 1.21 | "Setup: `pip install -e .`" (§19) | Failed: `build-backend = "setuptools.backends._legacy:_Backend"` doesn't exist. **FIXED** (`setuptools.build_meta`). |
| 1.22 | `python -m autoapply.cli apply` runs the application engine (§19) | Could not run: `candidate/manager.py:12` contained `\"\"\"` outside a string (a SyntaxError), plus the missing import in 1.9. The SyntaxError went away with `manager.py`'s rewrite; 1.9 is still **OPEN**. |
| 1.23 | "Playwright … handles complex ATS DOMs flawlessly" (§16) | Overstated. Each applier uses hardcoded selectors (e.g. `#submit_app`, `#first_name`). None has been exercised against a live form by any test; the only fixture is `tests/mock_ats.html`. |

---

## 2. Bugs that silently corrupt data, ranked by blast radius

"Silent" means no exception reaches the user: the wrong value is stored or sent and the run
reports success.

### 2.1 Appliers send fabricated or misplaced data to employers — **OPEN**
This has the widest blast radius because it is external and can't be undone: once submitted, a
recruiter sees it.
- Greenhouse, SmartRecruiters and Workable split `full_name` on the first space. A one-word name
  submits last name **`"Applicant"`**. A middle name ends up in the last-name field.
- `lever.py:57` fills `input[name="org"]` (Lever's *current company* field) with the candidate's **location**.
- `greenhouse.py:146-148` answers any github/portfolio/website question with the LinkedIn URL when the others are empty.
- `question_engine.py` accepts any Gemini answer whose self-reported confidence isn't `UNKNOWN`. Nothing checks it against the Vault.

### 2.2 Dedup merges different jobs and drops the second one — **OPEN**
`dedup.py:36-46` buckets locations coarsely:
- Any location containing "india", "bengaluru" or "bangalore" becomes `india`. "SDE Intern, Bengaluru, India" and "SDE Intern, Pune, India" at the same company hash identically, so the second is discarded (only `last_seen` changes).
- `"ca" in loc` sends Cambridge, Jamaica, Africa, Caracas, etc. to the `us` bucket.
- Every other location is truncated to 10 normalized characters.

For an India-focused search this silently loses real, distinct openings. Numeric `source_id`s
(Greenhouse, SmartRecruiters) fall through to this lossy title+location hash. It also merges
jobs *within* one company board.

### 2.3 Guessed board tokens attribute other companies' jobs to the wrong company — **OPEN**
`career_pages.py` falls back to `company.lower()` as the board token for the 39 entries without
one, then takes the **first** ATS that returns HTTP 200. Generic tokens (`embed`, `sep`, `evr`,
`flint`, `zip`, `revel`, `primer`, `barnes`, `antares`, `apollo`, `gemini`, …) can resolve to an
unrelated employer's board. Every job there is then stored under the wrong company name and
queued for application. `career_pages.json` also lists Plaid and GitLab twice.

Confirmed in this run (§5):
- The `primer` token (meant as the AI company Primer) returned an education company's board: "Admissions Representative", "Aftercare Program Guide".
- `barnes` returned a law firm's board: "2027 2L Summer Associate".

Both sets of jobs are stored under the seed's company name.

The opposite failure happens too. SmartRecruiters' postings API returns **HTTP 200 with an empty
list for any token**, including companies that don't use it (seen in this run:
`smartrecruiters_board_found company=Retool count=0`, the same for Rippling). The scanner treats
that as "board found" and stops, so Workable is never probed for any company that missed on
Greenhouse, Lever and Ashby.

### 2.4 The main source stores markup or nothing as `description_text` — **OPEN**
`CareerPagesSource` produces the bulk of ingested jobs, yet it never calls `html_to_text`:
- Greenhouse `content` is HTML-entity-escaped HTML (`&lt;p&gt;…`), and Ashby `descriptionHtml` is HTML. Both are stored verbatim as *text*.
- SmartRecruiters and Workable store `''`.

The classifier runs on this field. The internship check reads `desc[:200]` and the target-field
check reads `desc[:500]`, both on markup here. The YoE and PhD-only checks see nothing for
SmartRecruiters and Workable, so senior-YoE and PhD-only internships from those boards pass
unchallenged. Everything downstream (the dashboard, LLM prompts) receives markup.

### 2.5 First sighting wins forever — **OPEN**
On a dedup hit, `ingest_job` returns the existing row and only updates `last_seen`
(`job_service.py:31-34`):
- A rejected row (`is_active=0`) permanently shadows the job: rule fixes never reach it.
- A LinkedIn copy (no description, LinkedIn apply URL, no applier) blocks the same job arriving later from the company's ATS with a full description and a supported applier.
- Richer fields from later sources are thrown away, and the extra source isn't recorded anywhere.

### 2.6 The location rule rejects the jobs the candidate can actually take — **OPEN**
`filter.py:128-132` auto-rejects **Mumbai on-site** internships. The candidate's real constraint
is the opposite: on-site is possible *only* in Mumbai. So every on-site role they can accept is
`AUTO_REJECT`ed, while on-site roles in cities they can't work in are accepted. The orchestrator
even searches LinkedIn for `Mumbai` specifically (`orchestrator.py:20`), then the filter discards
the results. This is a policy bug rather than a code crash, but its effect is silent data loss.

### 2.7 `html_to_text` wrote a literal backslash-n — **FIXED**
`separator="\\n"` joined text blocks with the two characters `\` `n`: `"<p>a</p><p>b</p>"` became
`'a\\nb'` (verified). Affected sources: RemoteOK, Remotive and Arbeitnow (plus the unused
Greenhouse/Ashby/Indeed adapters). Every description *from those sources* is one mangled line.

Correction to the task brief: this did **not** touch every stored description.
`CareerPagesSource` (2.4) and LinkedIn (no description at all) never call `html_to_text`.

**Fix:** real `"\n"` separator. The test `tests/test_http_client.py::test_html_to_text_emits_real_newlines` asserts real newlines and no `\\n`.
Existing rows are not rewritten; re-run discovery on a fresh DB, or re-derive the text from `description_raw`.

### 2.8 `work_mode` was `unknown` for every job — **FIXED**
`guess_work_mode`'s `def` line had been deleted, leaving its body as unreachable code after
`return` inside `html_to_text`. `ingest_job` never set `work_mode`, so the Enum default
`UNKNOWN` was stored for every row.

**Fix:** restored as `guess_work_mode(text) -> str` in `sources/http_client.py`. `ingest_job` now
uses a source-supplied `work_mode` if one is given, otherwise it guesses from `location`.
Tests: `tests/test_http_client.py`, `tests/test_job_service.py`.

**Known limitation (unchanged, by design of the original function):** any non-empty location without "remote" or "hybrid" is classified `onsite`. Many LinkedIn and ATS locations are bare city names for roles that are actually remote or hybrid.

### 2.9 Dashboard edits to some Vault fields are silently dropped — **OPEN**
`dashboard/app.py:267` sets `specialization` and `enrollment_status` on `VaultEducation`, and
`:310` sets `results` on `VaultProject`. None of these columns exist, so `setattr` creates plain
Python attributes that are never persisted. The user sees "saved" and the data is gone.

### 2.10 Working-directory-dependent paths — **OPEN**
- `career_pages.py` reads `os.path.join(os.getcwd(), 'career_pages.json')`. Run from any other directory, it silently scans **zero** companies.
- `config._find_project_root()` walks up from the CWD. Run outside the repo, it silently creates and uses a different `data/autoapply.db`.
- Resume uploads are saved to the relative path `data/resumes/<uuid>`. An applier started from another CWD can't find the file, and the upload is skipped with only a warning.

### 2.11 Tests pollute the production DB — **OPEN**
`tests/test_e2e.py` commits a "MockInc / E2E Mock Job" row and an application into
`data/autoapply.db` on every run.

### 2.12 Latent: `posted_date` / `deadline` strings into `DateTime` columns — **OPEN**
`SourceResult` types these as `str`. SQLAlchemy's SQLite `DateTime` rejects strings, so any
source that starts populating them will have **every** job fail to ingest. Each failure only
logs `job_ingest_failed` at warning level. No source sets them today.

---

## 3. Dead code

### 3.1 Empty modules — **FIXED (deleted)**
`autoapply/intelligence/analyzer.py`, `autoapply/candidate/question_catalog.py`,
`autoapply/appliers/vault_integration.py`, `autoapply/candidate/auto_populate.py`,
`autoapply/candidate/cv_parser.py`. Nothing imported any of them, so no imports needed removing.
`autoapply/intelligence/` is now gone.

Still empty (not deleted; test scaffolding, outside `autoapply/`): `tests/conftest.py`,
`tests/test_adapters.py`, `tests/test_config.py`, `tests/test_models.py`, `tests/test_services.py`.
`README.md` is empty. `README(20260926-174401).md` is a prompt log, not a README.

### 3.2 Duplicate candidate store — **FIXED (deleted)**
`models/candidate.py` (`CandidateProfile`, `CandidateAnswer`) was a second candidate store:
- The appliers read `CandidateProfile`, synced from a hardcoded Windows JSON path.
- The dashboard writes the `Vault*` tables.

So what the user entered in the dashboard never reached an application. `CandidateAnswer`
duplicated `VaultAnswer` and nothing referenced it. Both were removed, and every consumer now
reads the Vault. Consequences are listed in §4.

### 3.3 Unused code paths — **OPEN**
- **Sources never instantiated:** `sources/greenhouse.py`, `sources/lever.py`, `sources/ashby.py`, `sources/indeed_rss.py`.
- **Registry never used:** `sources/registry.py`, the whole module.
- **Appliers never registered:** `appliers/breezy.py`, `appliers/workable.py`.
- **Model never read or written:** `models/intelligence.py` (`ApplicationIntelligence`).
- **Module never imported:** `security.py`.
- **Unreachable branches and dead values:**
  - `dashboard/app.py:65` counts `classification_category == 'ENTRY_LEVEL'`, which the filter never produces, so it's always 0.
  - `filter.py` reads `job_data['employment_type']`, which isn't a `SourceResult` field, so it's always `''`.
  - `greenhouse.py`'s `hasattr(profile, 'raw_json')` guard was always true (removed with 3.2).
- **Columns never written by any code path:**
  - `jobs`: `responsibilities`, `requirements`, `preferred_qualifications`, `salary_min/max/currency`, `compensation_text`, `posted_date`, `deadline`, `company_info`, `posting_snapshot`, `closed_at`.
  - `applications`: `confirmation_screenshot`, `cover_letter_used`, `other_materials`, `error_details`, `posting_snapshot_at_apply`.
- **Settings never read:**
  - `SchedulerSettings`: no scheduler exists.
  - `BrowserSettings`: `playwright_utils.py` hardcodes `headless=True` and a fresh context.
- **Unused templates:** `vault_dashboard.html`, `vault_documents.html`, `vault_identity.html`.
- **Unused config files:** `sources_config.yaml`, `search_config.yaml`, and `config.yaml` (empty).

### 3.4 Dependencies — **mostly OPEN**
| Package | Status |
|---|---|
| `alembic` | Declared, unused. No migrations directory; schema changes rely on `create_all`, which never alters existing tables. |
| `apscheduler` | Declared, unused. |
| `aiofiles` | Declared, unused. |
| `requests` | Imported by `career_pages.py`, **not declared**. **FIXED**: ported to `HttpClient`; no longer imported anywhere. |
| `google-genai` | Imported lazily by `question_engine.py`, not declared. |
| `cryptography` | Imported by the unused `security.py`, not declared. |
| `playwright` | Only an optional extra, but imported at module level by every applier. |
| `jinja2`, `python-multipart`, `lxml` | Not imported directly, but **used**: FastAPI templates, form parsing, and the BeautifulSoup parser. Keep. |

---

## 4. What deleting `CandidateProfile` broke (not papered over)

1. **Data in `candidate_profile.json` is not migrated.** Anyone relying on it must re-enter it at `/profile`. Existing databases keep orphaned `candidate_profiles` / `candidate_answers` tables; there is no migration tooling to drop them (see 3.4, alembic).
2. **Fields with no Vault equivalent are gone from the apply path:** `resume_text`, `cover_letter_template`, `certifications`, `extra_fields`, and any arbitrary keys in the JSON that the question engine read through `raw_json` (e.g. work authorization, notice period, relocation). Those now have to exist as `VaultAnswer` rows with `status='CONFIRMED'`. Otherwise Gemini receives no such fact and should return `UNKNOWN`, which **increases application stops** until the Vault is filled.
3. **Unconfirmed answers are deliberately excluded.** `VaultIdentity.to_profile_dict()` passes only `CONFIRMED` answers, so `NEEDS REVIEW` ones (including "CV Extracted") no longer reach the question engine.
4. **`full_name` now includes the middle name** (first + middle + last). Combined with bug 2.1's split-on-first-space, a middle name lands in the employer's last-name field.
5. **`location` is now `"city, country"`** from the Vault. Lever still writes it into the current-company field (2.1).
6. **Resume path.** `resume_path` is the active `VaultResume.file_path`, a CWD-relative path (2.10). With no active resume, nothing is uploaded, and most ATS forms will then fail on submit.
7. **Only one identity is supported** (`query(VaultIdentity).first()`).
8. **The apply engine still does not start**, for pre-existing reasons unrelated to this change: the missing `reset_stalled_applications` (1.9), and `playwright` not installed by the base `pip install -e .`. `manager.py` itself now imports cleanly. Before, it was a SyntaxError.
9. **`test_e2e.py` now raises `RuntimeError`** when the Vault is empty. Before, it would crash with `AttributeError` on a `None` profile. It still writes to the real DB (2.11).

---

## 5. End-to-end run on Linux (this commit)

Ubuntu, Python 3.12.3, fresh venv:
1. `pip install -e '.[dev]'` succeeded, after fixing `build-backend`.
2. `python -m autoapply.cli init` created `data/autoapply.db`.
3. `python -m autoapply.cli discover` ran in **27m48s** and exited 0, with 0 `source_failed` and 0 `job_ingest_failed`.

**Jobs ingested: 20,608 rows. Only 275 are active (`AUTO_ACCEPT`); 20,333 are `AUTO_REJECT`.**
The sources yielded 43,119 results in total. The remaining 22,511 were dedup hits: repeated
LinkedIn/RemoteOK queries, and Plaid and GitLab scanned twice.

| Source | Rows | Active |
|---|---:|---:|
| career_pages | 19,903 | 163 |
| linkedin | 264 | 108 |
| arbeitnow | 326 | 4 |
| remoteok | 99 | 0 |
| remotive | 16 | 0 |

Only **53 active jobs** have an Indian location (India, Bengaluru or Mumbai in the location string).

`work_mode` now: ONSITE 17,928 · REMOTE 2,053 · HYBRID 604 · UNKNOWN 23. Before this commit, every row was UNKNOWN.

**Top 10 `reject_reason` values by frequency.** A job's reasons are joined with ` | `, so these are full strings:

| # | Count | reject_reason |
|---:|---:|---|
| 1 | 3,586 | Does not match target technical fields \| Job is not explicitly an internship |
| 2 | 3,464 | Does not match target technical fields \| Senior role keywords \| Not explicitly an internship |
| 3 | 1,955 | Non-technical keywords in title \| Does not match target fields \| Not explicitly an internship |
| 4 | 1,627 | Non-technical keywords \| Does not match target fields \| Senior role keywords \| Not explicitly an internship |
| 5 | 1,559 | Senior role keywords \| Not explicitly an internship |
| 6 | 1,454 | Job is not explicitly an internship |
| 7 | 740 | Does not match target fields \| Senior role keywords \| Requires 5.0 years \| Not explicitly an internship |
| 8 | 384 | Does not match target fields \| Senior role keywords \| Requires 8.0 years \| Not explicitly an internship |
| 9 | 350 | Non-technical keywords \| Does not match target fields \| Senior role keywords \| Requires 5.0 years \| Not explicitly an internship |
| 10 | 348 | Does not match target fields \| Requires 5.0 years \| Not explicitly an internship |

Individual reasons (one job can carry several):

| Count | Reason |
|---:|---|
| 19,792 | Not explicitly an internship |
| 15,879 | Does not match target technical fields |
| 11,109 | Senior role keywords |
| 5,899 | Requires too much experience |
| 5,763 | Non-technical keywords in title |
| 46 | PhD-only |
| **24** | **"Mumbai non-remote internship"**: on-site Mumbai internships wrongly rejected by bug 2.6 |

**What the run says:**
- 97% of the volume is full-time roles scraped from mostly US company boards (`career_pages`) and then thrown away. That is where the 28 minutes go.
- The internship yield is 275 jobs, and only 53 of them are in India.
- The Mumbai rule (2.6) rejected 24 internships that are exactly in the candidate's allowed area.

**Data-quality counts:**
- 12,219 `career_pages` rows hold HTML in `description_text` (2.4).
- 1,336 rows have an empty description (2.4).
- 26 rows still contain a literal `\n`. Checked: it comes from the upstream `career_pages` text itself (e.g. Robinhood's posting contains the characters `\n`), which that source stores without `html_to_text`. It is not the fixed bug.

**HTTP errors:**
- 9 requests failed all retries: network errors on the Lever, Ashby, Greenhouse, SmartRecruiters and Workable APIs.
- 2 LinkedIn 429s.
- 1 SmartRecruiters 400.
