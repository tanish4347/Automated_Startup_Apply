# ARCHITECTURE.md

This document describes what the code **actually does today**. Planned work is marked
*(planned)*. For the full list of known defects, see [docs/AUDIT.md](docs/AUDIT.md).

## 1. Overview

A local Python system that discovers internship postings, stores them in SQLite, and has
Playwright-based appliers for a few ATSs. Search is focused on internships. The candidate can
work on-site only in Mumbai (incl. Navi Mumbai and Thane) and remotely from anywhere.

```text
config/search.yaml ──► `discover`: HTTP tier (sequential, one source at a time)
config/politeness.yaml   │  Unstop · Internshala · Instahyre · Himalayas · Adzuna (keyed)
                         │  RemoteOK · Remotive · Arbeitnow · LinkedIn guest (100 req/day) · ats_boards (resolved ATS boards)
                         │
                       `discover --browser`: browser tier, each source in its own process,
                         │  hard timeout, per-source cadence: Naukri · Wellfound · YC WaaS
                         ▼       (every run logged in source_runs: counts, requests, ok/degraded/failed)
                       filter.evaluate_job   (rules: internship, field, seniority, YoE, PhD,
                         │                    pay detection, location policy → location_fit)
                         ▼
                       dedup + ingest ──► SQLite (jobs, applications, vault_*)
                                             ▲                    │
                           dashboard (FastAPI + Jinja) ◄──────────┘
                                             │
                       apply engine (Playwright appliers) ── reads Vault ── Gemini for custom questions
```

## 2. Components

| Area | Location | State |
|---|---|---|
| CLI | `autoapply/cli.py` | `init`, `status`, `discover`, `apply`, `dashboard` |
| Settings | `autoapply/config.py` | Env/`AUTOAPPLY_*` settings + `SearchConfig` (pydantic) loaded from `config/search.yaml` |
| Discovery | `autoapply/sources/orchestrator.py` | Runs enabled sources **sequentially** (no thread pool) |
| Active sources | `sources/remoteok.py`, `remotive.py`, `arbeitnow.py`, `linkedin.py`, `ats_boards.py` | LinkedIn: guest HTML search only, no descriptions. `ats_boards`: fetches jobs only from companies whose ATS the resolver has verified (priority/Indian first, up to `ats_boards_max_companies` per run); never probes |
| Company universe | `discovery/universe.py`, `portfolios.py`, `directories.py`, `github.py`; `config/universe.yaml`, `portfolios.yaml`, `companies.yaml` | `autoapply universe seed [--source X]`. Seeders: own_data (job companies), manual (`companies.yaml`, incl. the 274 migrated `career_pages.json` entries), portfolios (18 VC pages, 4 extraction strategies), yc (India, via the yc-oss mirror), inc42 (Datalabs sitemaps + JSON-LD, resumable, capped per run), startup_india (API returning 503: skipped), github (India-located orgs; needs `GITHUB_TOKEN`). Dedup on website domain then normalized name; every differently-spelled merge is a `company_aliases` row (`autoapply universe merges`); `companies.seed_sources` lists every seeder that produced a company |
| ATS resolver | `discovery/ats_resolver.py`, `ats/patterns.py`, `ats/harvesters.py` | `autoapply ats resolve --limit N` (own cadence, separate from job fetching) / `ats stats`. Order: ATS host in the company's own job URLs → careers page (homepage links, /careers, /jobs, …) redirect or embedded iframe/script → validated API probes (Indian ATSs first; Greenhouse board name must match; SmartRecruiters needs >0 jobs) → `custom` careers page. Re-checked after 30 days; failures back off 2^n days; after 4, `needs_generic_crawl`. Fetchers: Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Keka, Zoho Recruit, Freshteam, MyNextHire, Workday, Personio, Teamtailor, Breezy (+ Darwinbox via browser). Detected only (generic crawler): Turbohire, BambooHR, JazzHR, Pinpoint, Recruiterflow, Skillate, SpringRecruit, HirePro |
| Indian boards | `sources/unstop.py`, `internshala.py`, `instahyre.py`, `himalayas.py`, `adzuna.py` | Plain HTTP, no browser. Each module's docstring records the endpoint and the parameters found by probing it. Unstop: public search API, all ~700 open internships (no server-side category filter). Internshala: category HTML pages; detail pages only for cards that pass the filter, cached in `data/cache/internshala/`. Instahyre: public API, list-only (no descriptions reachable); its `employer{}` seeds `companies`. Himalayas: `/jobs/api/search?employment_type=Intern`. Adzuna: needs `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` in `.env`, skipped otherwise |
| Browser tier | `sources/browser.py`, `naukri.py`, `wellfound.py`, `yc_waas.py` | For platforms that reject plain HTTP. Persistent Chromium profile per platform in `data/browser_profiles/<platform>/` (`autoapply browser-login <platform>` opens it headed for a one-time manual login/challenge). Loads the site's page, captures its own XHR headers, then calls the JSON endpoints with `fetch()` inside the page. Every navigation/fetch is budgeted and paced; a CAPTCHA/challenge raises `ChallengeDetected` (never solved) and the run is marked `degraded`. XHR calls are logged to `data/browser_profiles/<platform>.calls.jsonl` for reverse-engineering. Naukri: `/jobapi/v3/search` with the page's `appid/systemid/clientid/gid/nkparam` headers; details from the job page's own `/jobapi/v4/job` response (full JD + direct ATS apply link). Wellfound: logged-out SEO pages' `__NEXT_DATA__` Apollo cache. WaaS: Inertia `data-page` props (a fixed 30-job teaser until you sign in) |
| Politeness | `autoapply/politeness.py`, `config/politeness.yaml`, `models/source_run.py` | Per-source daily request budget (summed from `source_runs` per UTC day), randomised delays with periodic long pauses, browser cadence and run timeout |
| LinkedIn policy | `sources/linkedin.py`, `appliers/registry.py` | Guest HTML only, 100 requests/day. An httpx request hook raises `LinkedInAuthViolation` on any LinkedIn session cookie, `Authorization` header or non-GET request; the orchestrator re-raises it. Refuses to run if LinkedIn credentials are in the environment. Never a browser platform; the applier registry refuses LinkedIn appliers and `find_applier` returns nothing for LinkedIn-hosted jobs |
| Unused sources | `sources/greenhouse.py`, `lever.py`, `ashby.py`, `indeed_rss.py`, `registry.py` | Never instantiated |
| HTTP | `sources/http_client.py` | Rate limit + retry/backoff; `html_to_text`, `guess_work_mode` |
| Filter | `sources/filter.py` | Rule-based classifier. See §3 |
| Location | `sources/location.py` | Normalizes messy location strings → canonical city / country / work mode (Indian aliases + Mumbai-area localities) |
| Dedup | `services/dedup.py` | v2: normalized company + order-insensitive title + place (city, raw location, or `remote-<country>`). Deliberately under-merges |
| Ingest | `services/job_service.py` | Links/creates the company, records a sighting. On a duplicate: fills missing fields, upgrades to a direct ATS link, never overwrites |
| Companies | `models/company.py`, `services/company_service.py` | `companies` (durable: website/domain, ATS type/token + resolution state, is_india, priority, seed_sources, …), `company_aliases`, `job_sightings` |
| Applications | `services/application_service.py` | State machine: DISCOVERED → QUEUED → IN_PROGRESS → SUBMITTED/FAILED → ASSESSMENT/INTERVIEW/OFFER/REJECTED → CLOSED. There is **no** `MANUAL_REQUIRED` state |
| Appliers | `appliers/` | Registered: Greenhouse, Lever, Ashby, SmartRecruiters. `workable.py` and `breezy.py` exist but are not registered. The apply engine **cannot start**: `appliers/orchestrator.py` imports `reset_stalled_applications`, which does not exist (AUDIT 1.9) |
| Custom questions | `appliers/question_engine.py` | **Google Gemini** (`gemini-2.5-flash`, needs `GEMINI_API_KEY`). Sends the Vault profile + question and trusts the model's self-reported confidence. No embeddings or canonical-key mapping yet *(planned)* |
| Candidate data | `models/vault.py`, `candidate/manager.py` | Vault tables are the single candidate store (edited on the dashboard). `VaultIdentity` exposes `full_name`, `location`, `resume_path`, `to_profile_dict()` for the appliers |
| Intake | `candidate/cv_parser.py`, `catalog.py`, `intake.py`, `sensitive.py`; `/intake`; `autoapply vault seed / import-cv PATH / status` | CV parser (pdfplumber, deterministic) writes Vault facts with source CV / NEEDS REVIEW. The question catalog (`vault_answer`) is seeded only from `docs/QUESTIONS.md`. `/intake`: one card at a time (Enter confirms, arrows move, Esc skips), four passes: CV facts, gaps, policy rules (`vault_policy`, structured), story bank. SENSITIVE keys (work authorisation, sponsorship, criminal history, demographics, grades, graduation date, stipend, notice period, start date): a before_flush guard rejects any write outside `user_input()` (only the intake/profile/vault UI and CLI enter it); CV values for them are suggestions only; the answer engine returns them verbatim or raises `ParkApplication` (application status `parked`) and never shows them to the model |
| Dashboard | `dashboard/app.py` | FastAPI + Jinja2 SSR, Tailwind via CDN |
| DB / migrations | `models/`, `autoapply/migrations/` | SQLite via SQLAlchemy, WAL mode, `busy_timeout=5000`. Schema is managed by Alembic |
| Unused | `models/intelligence.py`, `security.py` | Never read or imported |

### Stub modules
These modules were empty. They were deleted in the fix commit before this one:
`intelligence/analyzer.py`, `candidate/question_catalog.py`, `appliers/vault_integration.py`,
`candidate/auto_populate.py`, `candidate/cv_parser.py`.

Still empty: `config.yaml` (read as an optional settings override, but has no content),
`tests/conftest.py`, `tests/test_adapters.py`, `tests/test_config.py`, `tests/test_models.py`,
`tests/test_services.py`, `README.md`.

## 3. Discovery rules (`config/search.yaml` + `sources/filter.py`)

- **Hard rejects** (`AUTO_REJECT`, stored with `is_active=0` and a `reject_reason`):
  - not explicitly an internship;
  - non-technical title;
  - no target-field match;
  - senior title;
  - >3 years of experience required;
  - PhD-only.
- **Target and exclude keywords** come from `config/search.yaml`.
- **Location policy** (`location_policy` in `config/search.yaml`) never rejects. It sets `jobs.location_fit`:
  - `ok`: remote (policy allows remote), or the location names an on-site city (Mumbai, Navi Mumbai, Thane);
  - `outside_policy`: on-site/hybrid anywhere else. These jobs are stored and visible but **not auto-queued** (no application row is created);
  - `unknown`: no location given.
- **Pay** (`detect_pay`): `UNPAID` ("unpaid", "no stipend") → `PAID` with an amount (₹ / Rs / INR / $ / € / £, ranges, "15k/month", per month/week/hour/year) → `UNKNOWN` for "performance based" with no fixed amount → `PAID` for the words "stipend", "salary", "paid internship" → otherwise `UNKNOWN`. Amounts are stored as `stipend_*`.
- **Enrichment** (stored on `jobs`): city, country, `role_family` (swe/ml/ds/data_eng/research/other), `duration_months`, `apply_channel` (ats_direct/internshala/naukri/wellfound/linkedin_easy/email/google_form/unknown), and stipend min/max/currency/period. `start_date` exists but no source fills it yet.
- **Work mode**: a source-supplied value wins. Otherwise it is detected from location + title (remote / WFH / hybrid), with `guess_work_mode(location)` as the ingest fallback.

## 4. Database

Tables:
- `jobs` (N:1 → `companies`)
- `job_sightings` (N:1 → `jobs`)
- `companies`
- `applications` (N:1 → `jobs`)
- `vault_identity`, `vault_education`, `vault_employment`, `vault_project`, `vault_skill`, `vault_resume`, `vault_answer`
- `application_intelligence` (unused)

Migrations live in `autoapply/migrations/versions/`:
- `0001_baseline`: the schema previously created by `create_all`.
- `0002_location_fit`: adds `jobs.location_fit`.
- `0003_companies_sightings`: adds `companies`, `job_sightings` and the job enrichment columns.
- `0004_backfill_dedup_v2`: data only. Links companies, adds sightings, fills enrichment, and rehashes with dedup v2. Colliding rows are marked `MERGED` and inactive, never deleted.

`python -m autoapply.cli init` runs `alembic upgrade head`. A pre-Alembic DB is stamped at
`0001_baseline` first, so existing data is kept. You can also run `alembic upgrade head`
directly; the URL comes from `AUTOAPPLY_DB_URL` or the default `data/autoapply.db`.

## 5. Running

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev,browser]'
.venv/bin/playwright install chromium          # only needed for the apply engine
.venv/bin/python -m autoapply.cli init          # create / migrate the DB
.venv/bin/python -m autoapply.cli universe seed        # build the company universe
.venv/bin/python -m autoapply.cli ats resolve --limit 300   # identify ATSs (slow cadence)
.venv/bin/python -m autoapply.cli discover      # run discovery
.venv/bin/python -m autoapply.cli dashboard     # http://127.0.0.1:8080
.venv/bin/pytest --ignore=tests/test_e2e.py --ignore=tests/test_career.py   # offline tests
```

`tests/test_career.py` hits live ATS APIs. `tests/test_e2e.py` needs Playwright and writes to
the real DB (AUDIT 2.11).

## 6. Known limitations

The main ones are below; everything else is in [docs/AUDIT.md](docs/AUDIT.md).
- A later, richer sighting fills fields but does not re-run classification (2.5).
- Guessed ATS tokens can attribute jobs to the wrong company (2.3).
- ATS harvesters store plain-text descriptions; the old `career_pages` source (HTML in `description_text`, AUDIT 2.4) is gone.
- Appliers can submit fabricated or misplaced values (2.1).
- LinkedIn guest search rate-limits aggressively and returns no descriptions.
- No CAPTCHA handling. Workday is not supported.
