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
                         │  LinkedIn guest (100 req/day) · ats_boards (resolved ATS boards)
                         │  (RemoteOK · Remotive · Arbeitnow · YC WaaS: disabled in search.yaml, 0 in-policy yield)
                         │
                       `discover --browser`: browser tier, each source in its own process,
                         │  hard timeout, per-source cadence: Naukri · Wellfound
                         ▼       (every run logged in source_runs: counts, requests, ok/degraded/failed)
                       filter.evaluate_job   (rules: internship, field, seniority, YoE, PhD,
                         │                    pay detection, location policy → location_fit)
                         ▼
                       dedup + ingest ──► SQLite (jobs, applications, vault_*)
                                             ▲                    │
                           dashboard (FastAPI + Jinja) ◄──────────┘
                                             │
                       apply engine: harness (review_only by default) + appliers ── answer engine ── Vault
```

## 2. Components

| Area | Location | State |
|---|---|---|
| CLI | `autoapply/cli.py` | `init`, `status`, `dashboard`, `discover [--browser] [--source X]`, `apply`, `answers`, `vault`, `questions`, `universe`, `browser-login`, `ats` |
| Settings | `autoapply/config.py` | Env/`AUTOAPPLY_*` settings + `SearchConfig` (pydantic) loaded from `config/search.yaml` |
| Discovery | `autoapply/sources/orchestrator.py` | Runs enabled sources **sequentially** (no thread pool) |
| Active sources | `sources/linkedin.py`, `ats_boards.py` (`remoteok.py`, `remotive.py`, `arbeitnow.py` exist but are disabled) | LinkedIn: guest HTML search only, no descriptions. `ats_boards`: fetches jobs only from companies whose ATS the resolver has verified (priority/Indian first, up to `ats_boards_max_companies` per run); never probes |
| Company universe | `discovery/universe.py`, `portfolios.py`, `directories.py`, `github.py`; `config/universe.yaml`, `portfolios.yaml`, `companies.yaml` | `autoapply universe seed [--source X]`. Seeders: own_data (job companies), manual (`companies.yaml`, incl. the 274 migrated `career_pages.json` entries), portfolios (18 VC pages, 4 extraction strategies), yc (India, via the yc-oss mirror), inc42 (Datalabs sitemaps + JSON-LD, resumable, capped per run), startup_india (API returning 503: skipped), github (India-located orgs; needs `GITHUB_TOKEN`). Dedup on website domain then normalized name; every differently-spelled merge is a `company_aliases` row (`autoapply universe merges`); `companies.seed_sources` lists every seeder that produced a company |
| ATS resolver | `discovery/ats_resolver.py`, `ats/patterns.py`, `ats/harvesters.py` | `autoapply ats resolve --limit N` (HTTP) then `ats resolve-browser` (the JS stage), on their own cadence. Order: ATS host in the company's own job URLs → careers page (homepage links, `careers.<domain>`, then `/careers`, `/jobs`, …) redirect or embedded script → validated API probes (Indian ATSs first; Euro/US SMB products last and never for Indian companies) → `custom`. **A probe counts only when the board has at least one open job**: every one of these products answers 200 with an empty list for an account that merely exists, which mis-assigned Workable to Zepto, Acko, Flipkart, Chargebee, Dream11, ShareChat, Dunzo and Curefit. A stored `careers_url` that is itself an ATS board URL is never re-used as evidence, or a wrong verdict would re-confirm itself at 0.95 forever. Browser stage: loads the careers page and its job-list sub-pages and reads the ATS out of the requests the page makes (Curefit → zwayam, ShareChat → mynexthire), one page per company so a late response cannot be mis-attributed. Re-checked after 30 days; failures back off 2^n days; after 4, `needs_generic_crawl` |
| Indian boards | `sources/unstop.py`, `internshala.py`, `instahyre.py`, `himalayas.py`, `adzuna.py` | Plain HTTP, no browser. Each module's docstring records the endpoint and the parameters found by probing it. Unstop: public search API, internships + jobs, 12 search terms, up to 40 pages. Internshala: category HTML pages; detail pages only for cards that pass the filter, cached in `data/cache/internshala/`. Instahyre: public API, list-only (no descriptions reachable); its `employer{}` seeds `companies`. Himalayas: `/jobs/api/search?employment_type=Intern`. Adzuna: needs `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` in `.env`, skipped otherwise |
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
| Applications | `services/application_service.py` | State machine: DISCOVERED → QUEUED → IN_PROGRESS → SUBMITTED/FAILED/PARKED → ASSESSMENT/INTERVIEW/OFFER/REJECTED → CLOSED. PARKED = waiting for /review or a missing SENSITIVE answer |
| Appliers | `appliers/orchestrator.py`, `registry.py` | Harness appliers (`HARNESS_APPLIERS`): Internshala only. Legacy `BaseApplier` appliers (Greenhouse, Lever, Ashby, SmartRecruiters; bespoke logic, AUDIT 2.1) are registered but parked unless their platform is `live`, so in practice never run. `workable.py`, `breezy.py` unregistered. Rewriting the four ATS appliers on the harness is in progress (see PROGRESS.md) |
| Legacy custom questions | `appliers/question_engine.py` | Google Gemini; used only by the legacy ATS appliers. Superseded by the answer engine |
| Candidate data | `models/vault.py`, `candidate/manager.py` | Vault tables are the single candidate store (edited on the dashboard). `VaultIdentity` exposes `full_name`, `location`, `resume_path`, `to_profile_dict()` for the appliers |
| Apply harness | `appliers/harness.py`, `review.py`, `resolve.py`, `config/apply.yaml`; `/review` | Every attempt: dedup-cluster claim (UNIQUE in `application_claims`; brand + title, so the same internship on two sources is applied to once), daily cap (40, per-platform sub-caps) counted from `applications.last_attempt_at`, jittered pacing. `submit_mode` per platform, `review_only` unless the config file says exactly `live` (no code writes it): review_only reuses `BrowserSession.read_only()` (every non-GET aborted), fills, screenshots, parks for `/review`. Evidence per attempt: full-page screenshot, filled-field diff, final URL, form fingerprint. Outcomes: submitted (platform's explicit success assertion), uncertain, parked, failed. Pre-harness ATS appliers run only where `live`. `resolve.py`: aggregator postings resolved once (Unstop -> its own registration; Himalayas behind Cloudflare: noted, not solved); RFC 9309 robots.txt matching |
| Answer engine | `answers/engine.py`, `entail.py`, `models/answer_alias.py`, `config/answers.yaml`; `autoapply answers explain --job-id N` | Tier 1: alias table (seeded from docs/QUESTIONS.md + the harvest) and cluster rules, then bge-small embeddings above a similarity floor (stricter for SENSITIVE keys, country guard); CONFIRMED vault answers used verbatim, NEEDS REVIEW never; SENSITIVE resolves only here or parks. Tier 2: Ollama (qwen3:8b) for finite answer spaces only, prompt = non-sensitive policy rules + question, reply must be one of the form's options; skipped when Ollama is down. Free text parks. Write-back: aliases, new NEEDS REVIEW questions, /review corrections as suggestions |
| Internshala applier | `appliers/internshala.py`; `autoapply questions dump-form --platform internshala` | Harness applier for apply_channel=internshala: posting -> Apply link (query stripped) -> form; login wall raises NeedsLogin; explicit success assertion (confirmation text AND URL left the form; provisional until a live submit is observed); `reachability()` checks postings via robots-allowed pages only |
| Intake | `candidate/cv_parser.py`, `catalog.py`, `intake.py`, `sensitive.py`; `/intake`; `autoapply vault seed / import-cv PATH / status` | CV parser (pdfplumber, deterministic) writes Vault facts with source CV / NEEDS REVIEW. The question catalog (`vault_answer`) is seeded only from `docs/QUESTIONS.md`. `/intake`: one card at a time (Enter confirms, arrows move, Esc skips), four passes: CV facts, gaps, policy rules (`vault_policy`, structured), story bank. SENSITIVE keys (work authorisation, sponsorship, criminal history, demographics, grades, graduation date, stipend, notice period, start date): a before_flush guard rejects any write outside `user_input()` (only the intake/profile/vault UI and CLI enter it); CV values for them are suggestions only; the answer engine returns them verbatim or raises `ParkApplication` (application status `parked`) and never shows them to the model |
| Dashboard | `dashboard/app.py` | FastAPI + Jinja2 SSR, Tailwind via CDN |
| DB / migrations | `models/`, `autoapply/migrations/` | SQLite via SQLAlchemy, WAL mode, `busy_timeout=30000`. Schema is managed by Alembic |
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
- `company_aliases`, `source_runs` (per-source run counts/budgets)
- `form_question` (harvested application-form questions), `question_alias` (answer-engine aliases)
- `application_attempts` (evidence per attempt), `application_claims` (UNIQUE dedup-cluster claim)
- `vault_identity`, `vault_education`, `vault_employment`, `vault_project`, `vault_skill`, `vault_resume`, `vault_answer`, `vault_policy`
- `application_intelligence` (unused)

Migrations live in `autoapply/migrations/versions/`:
- `0001_baseline`: the schema previously created by `create_all`.
- `0002_location_fit`: adds `jobs.location_fit`.
- `0003_companies_sightings`: adds `companies`, `job_sightings` and the job enrichment columns.
- `0004_backfill_dedup_v2`: data only. Links companies, adds sightings, fills enrichment, and rehashes with dedup v2. Colliding rows are marked `MERGED` and inactive, never deleted.
- `0005_source_runs` · `0006_company_universe` · `0007_ats_resolution` · `0008_form_question` · `0009_vault_intake` · `0010_apply_harness` · `0011_question_alias`.

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
- Legacy ATS appliers can submit fabricated or misplaced values (2.1); they only run in `live` mode.
- Himalayas posting pages are behind Cloudflare, so its in-policy jobs have no resolved apply channel.
- LinkedIn guest search rate-limits aggressively and returns no descriptions.
- No CAPTCHA handling. Workday is not supported.
