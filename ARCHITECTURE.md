# ARCHITECTURE.md

## 1. Project Overview
The Automated Startup Apply system is an end-to-end autonomous job discovery and application platform. Its primary objective is to continuously discover relevant internship opportunities across multiple platforms and ATS systems, intelligently deduplicate them, and ultimately automate the application process using the candidate's personal data, resume, and an intelligent knowledge base.

## 2. Core Architecture

```text
JOB SOURCES (Greenhouse, Lever, LinkedIn, RemoteOK, Remotive)
    ↓
JOB ACQUISITION (Concurrent HTTP API / RSS / HTML parsing)
    ↓
NORMALIZATION (Standardized models, text extraction)
    ↓
DEDUPLICATION (Cross-source canonical hashing)
    ↓
JOB DATABASE (SQLite with strict scoring / YoE / PhD exclusions)
    ↓
APPLICATION QUEUE (Prioritization & Status Tracking)
    ↓
APPLICATION AGENT (Playwright + Python)
    ↑ (Reads)
CANDIDATE KNOWLEDGE BASE (Vault: Facts, Story Bank, Preferences)
    ↓
APPLICATION DATABASE (State Machine, Error Tracking, Screenshots)
    ↓
DASHBOARD (FastAPI Server-Rendered UI)
    ↓
INTERVIEW / ASSESSMENT PREPARATION (Generative AI)
```

## 3. Major Components

- **JOB DISCOVERY**: Natively fetches job postings via APIs and HTML parsing from configured sources. Location: `autoapply/sources/`. 
- **SOURCE ADAPTERS**: Individual implementations (e.g., `greenhouse.py`, `linkedin.py`) mapping raw data into standard `SourceResult`.
- **JOB NORMALIZATION / FILTERING**: Computes an eligibility score based on target fields, Years of Experience (YoE), and strict internship classification. Location: `autoapply/sources/filter.py`.
- **DEDUPLICATION**: Identifies duplicate jobs across different platforms (e.g., LinkedIn vs. Greenhouse) using a global identity hash. Location: `autoapply/services/dedup.py`.
- **DATABASE**: Central SQLite database bridging all workflows (Jobs, Applications, Vault). Location: `autoapply/models/`.
- **APPLICATION QUEUE**: A state-machine managing job lifecycle (QUEUED -> IN_PROGRESS -> SUBMITTED/FAILED). Location: `autoapply/services/application_service.py`.
- **APPLICATION ROUTER / ATS ADAPTERS**: Playwright-based browser automation logic tailored for specific ATS systems (Greenhouse, Lever, etc.). Location: `autoapply/appliers/`.
- **CANDIDATE VAULT**: The structured data repository for the user's factual info, preferences, and story bank. Location: `autoapply/candidate/vault.py`.
- **QUESTION BRAIN / AI AGENT**: Synthesizes structured data from the Vault dynamically to map arbitrary employer questions into consistent answers using LLMs. Location: `autoapply/intelligence/`.
- **DASHBOARD**: Server-rendered FastAPI UI replacing the need for external tools to view jobs, trace applications, and update the Vault. Location: `autoapply/dashboard/app.py`.
- **CLI**: The main entry point to trigger discovery, the dashboard, and initialization. Location: `autoapply/cli.py`.
- **TESTING**: Simple verification scripts ensuring adapter connectivity. Location: `tests/`.

## 4. Job Discovery Architecture

- **Acquisition**: Jobs are fetched concurrently using a ThreadPoolExecutor in `orchestrator.py`.
- **Source Adapters**: Inherit from `BaseSource`. We employ direct REST API polling for ATS boards (Greenhouse, Lever, Ashby, Workable, SmartRecruiters) derived from a massive `career_pages.json` universe, as well as aggregator APIs (Remotive, RemoteOK) and guest HTML parsing (LinkedIn).
- **Normalization**: Fields are mapped to the canonical `Job` schema.
- **Filtering & Deduplication**: Filter scoring natively eliminates non-internship roles. Then `deduplicate_job` handles cross-source hashing. Rejected jobs are retained in the database (`is_active=0`) for auditability, avoiding blind spots.
- **Extensibility**: Adding a new source simply requires extending `BaseSource` and yielding `SourceResult` objects.

## 5. Current Job Search Rules

The current engine is strictly constrained to source **ONLY INTERNSHIP ROLES**. Entry-level/full-time roles are heavily penalized and auto-rejected.

- **Target Fields**: Data Science, Machine Learning, AI / GenAI, Data Analytics, NLP, Computer Vision, Research / AI Research, Data Engineering, ML Engineering, Software Engineering, Backend, Full Stack.
- **Exclusions**: 
  - Non-technical roles (Sales, HR, Marketing).
  - PhD/Doctoral-specific internships (e.g., "PhD candidates only").
  - Seniority roles (Lead, Manager, Principal, etc.) and roles requesting >2 Years of Experience.
  - Mumbai on-site/hybrid internships (Remote Mumbai internships are allowed).
- **Inclusions**: Remote internships, Non-remote internships (excluding Mumbai), Summer/3-month internships.

## 6. Application Architecture

- **Queue**: Tracks applications via an explicit state machine (`QUEUED`, `IN_PROGRESS`, `SUBMITTED`, `FAILED`, `MANUAL_REQUIRED`).
- **Browser Automation**: `autoapply/appliers/` utilizes Playwright to spin up headless/headed browsers, navigate to URLs, fill standard inputs natively, and use DOM parsing to interpret fields.
- **Resilience**: The system supports crash recovery and retry loops. If a form asks a question completely alien to the Vault, it gracefully halts, marks `MANUAL_REQUIRED`, captures a snapshot/screenshot, and preserves the session.

## 7. Candidate Knowledge Base

The Vault (`autoapply/models/vault.py` & `candidate_vault.json`/DB) separates candidate identity into:
1. **FACTS**: Hard truths (e.g., Name, Email, Graduation Year, GPA).
2. **PREFERENCES / POLICIES**: Application constraints (e.g., Target Salary, Visa requirements, Location preferences).
3. **STORY BANK**: Bulleted project and experience summaries.
4. **REUSABLE ANSWERS**: Hardcoded strings for specific common questions (e.g., "What is your GitHub URL?").

The Application Agent cross-references this Vault rather than hallucinating generic AI answers.

## 8. Dynamic Question Understanding

The system tackles the massive variance in ATS question framing via the Question Brain.
- **Extraction**: Playwright scrapes form labels.
- **Semantic Mapping**: An LLM or embedding step classifies the question (e.g., "Are you willing to relocate?" and "Would you move for this role?" both map to `relocation_preference`).
- **Resolution**: The Agent queries the mapped concept against the Vault. If confidence is high, it submits. If factual data is missing, it refuses to hallucinate and stops.

## 9. AI / LLM Architecture

- **Provider**: Uses OpenAI (e.g., GPT-4o-mini) and Anthropic (e.g., Claude 3.5 Haiku) dynamically configured in `.env`.
- **Purpose**: Strictly utilized for semantic classification (mapping ATS questions to Vault fields), rewriting Story Bank bullets to fit character constraints, or generating polite Cover Letter intros based *only* on provided facts.
- **Safety**: Prompt constraints explicitly instruct the LLM *never* to invent facts, skills, or employment history not found in the Candidate Vault.

## 10. Dashboard Architecture

- **Stack**: FastAPI server returning raw HTML via Jinja2 templates, utilizing Tailwind CSS via CDN.
- **Philosophy**: Chose Server-Side Rendering (SSR) to reduce complexity. The app does not require npm, webpack, or React. It's a single Python monolith for extreme stability.
- **Routing**: Clean RESTful routes (Jobs, Applications, Settings, Queue).

## 11. Database

- **Technology**: SQLite via SQLAlchemy.
- **Key Tables**:
  - `jobs`: Stores raw snapshots, standard schema (title, company, description), YoE, classification tracking, and pay status.
  - `applications`: Stores state transitions, URLs, failure traces.
  - `vault_entries`: Centralized candidate config.
- **Relationships**: `jobs` 1<->N `applications`.

## 12. Security

- **Secrets**: Ignored globally via `.gitignore`. API keys (`OPENAI_API_KEY`) live in `.env`.
- **Candidate Data**: Private resumes (`data/resumes/`) and the SQLite `.db` file are strictly `.gitignored`.
- **Cryptography**: A `.vault_key` can be used to locally encrypt highly sensitive text fields in the DB, though physical DB isolation is the primary defense.

## 13. Error Handling / Reliability

- **Network**: HTTP requests employ exponential backoff in `orchestrator.py` and `http_client.py`.
- **ATS Isolation**: If Greenhouse changes its DOM structure, only the `GreenhouseApplier` fails. The rest of the orchestrator proceeds.
- **Playwright Failures**: Handled via try/except timeouts. Stalled applications are safely rolled back to a retryable state or flagged.

## 14. Testing

- **Suite**: Contains `test_career.py` and `test_e2e.py` for verifying ATS endpoints and database interactions.
- **Limitations**: The test suite is currently functional/integration focused. It does not possess full unit-test coverage for every Playwright edge case.

## 15. Repository Structure

```
autoapply/
├── appliers/       # Playwright ATS scripts
├── candidate/      # Vault and Resume parsing
├── dashboard/      # FastAPI UI and Jinja templates
├── intelligence/   # Question parsing / LLM prompts
├── models/         # SQLAlchemy DB schemas
├── services/       # Core business logic (Dedup, Job, App)
├── sources/        # Job discovery & API adapters
├── cli.py          # Command Line Interface
tests/              # Test suite
career_pages.json   # ATS company tokens
config.yaml         # App config
search_config.yaml  # Discovery config
pyproject.toml      # Dependencies
```

## 16. Technology Stack

| Technology | Purpose | Where used | Reason for choice |
|---|---|---|---|
| Python 3.10+ | Core language | Everywhere | ML/LLM ecosystem maturity |
| FastAPI | Web framework | `dashboard/`, `cli.py` | High performance, simple SSR routing |
| SQLAlchemy | ORM | `models/`, `services/` | Bulletproof relational schema handling |
| SQLite | Database | `data/autoapply.db` | Zero-configuration local persistence |
| Playwright | Browser Automation | `appliers/` | Handles SPAs and complex ATS DOMs flawlessly |
| Pydantic | Schema Validation | `sources/`, LLM outputs | Type safety for API data and LLM structures |
| Jinja2 | HTML Templating | `dashboard/templates/` | Robust server-rendered UI |

## 17. Important Engineering Decisions

- **Relational DB over JSON**: Moved from JSON blobs to SQLAlchemy to handle massive deduplication queries, queue states, and lifecycle tracking reliably.
- **SSR Dashboard**: Dropped React/Next.js for FastAPI+Jinja2. Keeps the project as a single deployable Python package, eliminating build steps.
- **Job Scoring vs. Binary Filters**: Moved to a cumulative scoring engine to gracefully handle edge cases (e.g. good title but missing pay) instead of rigid booleans.
- **Full Snapshot Preservation**: Rejected jobs are kept with `is_active=0` and exact `reject_reason` strings to allow auditing of the search rules and prevent duplicate re-fetching.

## 18. Known Limitations

- **LinkedIn Strictness**: LinkedIn Guest API heavily rate-limits. We rely on targeted searches but can easily be blocked.
- **Unsupported ATS**: Workday forms are highly dynamic and often require manual intervention / 2FA.
- **CAPTCHAs**: No automatic CAPTCHA bypassing. If triggered, the application halts and flags `MANUAL_REQUIRED`.
- **LLM Latency**: Semantic mapping of every form field via OpenAI adds ~5-15 seconds per application.

## 19. How the System Runs

- **Setup**: `pip install -e .` & `playwright install`
- **Initialize DB**: `python -m autoapply.cli init`
- **Run Discovery (Background Search)**: `python -m autoapply.cli discover`
- **Run Application Engine**: `python -m autoapply.cli apply`
- **Launch UI**: `python -m autoapply.cli dashboard`
