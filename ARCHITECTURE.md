# ARCHITECTURE.md

This document describes what the code **actually does today**. Planned work is marked
*(planned)*. For the full list of known defects, see [docs/AUDIT.md](docs/AUDIT.md).

## 1. Overview

A local Python system that discovers internship postings, stores them in SQLite, and has
Playwright-based appliers for a few ATSs. Search is focused on internships. The candidate can
work on-site only in Mumbai (incl. Navi Mumbai and Thane) and remotely from anywhere.

```text
config/search.yaml ──► discovery orchestrator (sequential, one source at a time)
                         │  RemoteOK · Remotive · Arbeitnow · LinkedIn guest · career_pages.json ATS probe
                         ▼
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
| Active sources | `sources/remoteok.py`, `remotive.py`, `arbeitnow.py`, `linkedin.py`, `career_pages.py` | LinkedIn: guest HTML search only, no descriptions. `career_pages`: probes Greenhouse → Lever → Ashby → SmartRecruiters → Workable per company in `career_pages.json` |
| Unused sources | `sources/greenhouse.py`, `lever.py`, `ashby.py`, `indeed_rss.py`, `registry.py` | Never instantiated |
| HTTP | `sources/http_client.py` | Rate limit + retry/backoff; `html_to_text`, `guess_work_mode` |
| Filter | `sources/filter.py` | Rule-based classifier. See §3 |
| Location | `sources/location.py` | Normalizes messy location strings → canonical city / country / work mode (Indian aliases + Mumbai-area localities) |
| Dedup | `services/dedup.py` | v2: normalized company + order-insensitive title + place (city, raw location, or `remote-<country>`). Deliberately under-merges |
| Ingest | `services/job_service.py` | Links/creates the company, records a sighting. On a duplicate: fills missing fields, upgrades to a direct ATS link, never overwrites |
| Companies | `models/company.py`, `services/company_service.py` | `companies` (durable: ATS type/token, is_india, priority, …) and `job_sightings`. `cli companies-import` loads `career_pages.json` |
| Applications | `services/application_service.py` | State machine: DISCOVERED → QUEUED → IN_PROGRESS → SUBMITTED/FAILED → ASSESSMENT/INTERVIEW/OFFER/REJECTED → CLOSED. There is **no** `MANUAL_REQUIRED` state |
| Appliers | `appliers/` | Registered: Greenhouse, Lever, Ashby, SmartRecruiters. `workable.py` and `breezy.py` exist but are not registered. The apply engine **cannot start**: `appliers/orchestrator.py` imports `reset_stalled_applications`, which does not exist (AUDIT 1.9) |
| Custom questions | `appliers/question_engine.py` | **Google Gemini** (`gemini-2.5-flash`, needs `GEMINI_API_KEY`). Sends the Vault profile + question and trusts the model's self-reported confidence. No embeddings or canonical-key mapping yet *(planned)* |
| Candidate data | `models/vault.py`, `candidate/manager.py` | Vault tables are the single candidate store (edited on the dashboard). `VaultIdentity` exposes `full_name`, `location`, `resume_path`, `to_profile_dict()` for the appliers |
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
.venv/bin/python -m autoapply.cli companies-import   # load career_pages.json into companies
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
- `career_pages` stores HTML or empty descriptions (2.4).
- Appliers can submit fabricated or misplaced values (2.1).
- LinkedIn guest search rate-limits aggressively and returns no descriptions.
- No CAPTCHA handling. Workday is not supported.
