# PROGRESS.md

Single source of truth for project state. Last updated 2026-10-02. Branch `main`; check
`git log -1` and `git status` for the exact commit and any uncommitted work.

## Done (prompts 1–17)
- **Foundation:** config (`config/*.yaml`), location policy, Alembic migrations 0001–0011, company-first data model,
  dedup v2, data-corruption fixes (see docs/AUDIT.md for the original audit).
- **Discovery:**
  - HTTP sources: Unstop, Internshala, Instahyre, Himalayas, Adzuna (keyed), LinkedIn guest, `ats_boards`.
  - Browser tier: Naukri, Wellfound.
  - Politeness budgets and `source_runs`.
  - RemoteOK, Remotive, Arbeitnow and YC WaaS are disabled (0 in-policy yield).
- **Company universe and ATS resolver:**
  - About 5.7k companies.
  - The resolver probes require at least one open job.
  - A stored ATS board URL is not used as evidence.
  - The browser stage reads the ATS from page requests.
  - Result on a 20-company sample: 10 resolved (it resolved none before the fix).
- **Question harvest:**
  - `form_question` holds the fields harvested from Greenhouse, Lever, Ashby, SmartRecruiters, and Unstop (11 fields).
  - The clustered catalog is in `docs/QUESTIONS.md`.
- **Intake:**
  - CV parser.
  - The question catalog is seeded from docs/QUESTIONS.md.
  - `/intake` UI.
  - SENSITIVE `before_flush` guard plus a test.
- **Apply harness (prompt 12):**
  - Claims, caps and pacing.
  - review_only read-only browser.
  - Evidence capture.
  - Outcomes.
  - `/review`.
  - Link resolver (`appliers/resolve.py`).
- **Answer engine (13):** tier 1a aliases/rules, 1b embeddings, 1c vault, 2 Ollama entailment (finite answers only).
- **Internshala applier (14):** harness applier, review_only.
- **Discovery yield (15):** in-policy jobs went from 554 to 603.
  - internshala 366, himalayas 128, unstop 84, naukri 18, ats/career 3, instahyre 2, linkedin 1, wellfound 1.

- **Applier coverage (16):** every applier is a harness `FormApplier`, registered in `appliers/registry.py`
  (the LinkedIn guard and its tests kept). The legacy `BaseApplier` path, `question_engine.py` (Gemini),
  `playwright_utils.py`, `workable.py` and `breezy.py` are deleted.
  - Greenhouse, Lever, Ashby and SmartRecruiters were rewritten on the harness with explicit success assertions
    (provisional). Each was checked read-only against a real form on 2026-10-02: 20, 14, 22 and 14 fields, with
    the submit control found. Greenhouse uses the embed form; the old `.asterisk` check is gone. Lever's
    hCaptcha widget is tolerated on load and never solved. Ashby needs its GraphQL *queries* allowed through
    read-only mode (`read_only_allow`), while mutations stay blocked.
  - Unstop: multi-step form (`max_steps`, `next_control`). A Next that sends data halts review_only. An external
    `regn_type` / off-site click raises `Reroute`. "Application Closed" counts as closed.
  - Naukri: the Apply click is the submission (`docs/NAUKRI_FORM.md`). Company-site jobs reroute from stored
    links or the page's job JSON, without clicking.
  - Harness: `NeedsLogin` re-queues the application and skips that platform for the run. Tier-3 answers park
    until approved, even when the platform is `live`.
  - Himalayas: the company's own ATS board is found by its slug, and a job is matched on an exact unique title
    (`resolve.resolve_himalayas_boards`). This routed 25 jobs: 14 SmartRecruiters, 9 Greenhouse, 2 Lever.
  - `autoapply appliers coverage|list|resolve`. **Coverage on 2026-10-02: 483 of 603 in-policy jobs have an
    applier** (internshala 366, unstop 84, smartrecruiters 14, greenhouse 10, naukri 5, ashby 2, lever 2).
    120 have none: 104 Himalayas with no supported board or no title match, 12 company_site, 2 instahyre, 1 Workday, 1 wellfound.
- **Generative tier + company brief (17):** migration 0012 adds `companies.company_brief` (it did not exist
  before) and `generated_answer`.
  - `services/company_brief.py` builds a brief once per company from database facts plus text lifted from its
    site (homepage and about page). It respects robots.txt, uses the `company_brief` budget, and is cached
    permanently. Run `autoapply companies brief`. The same brief is shown on /interview-prep.
  - `answers/generate.py` is tier 3. Retrieval is the story bank plus the brief. Ollama generates a draft, then
    a separate verification call lists each claim with a quote. Deterministic checks then require the quotes,
    numbers and names to appear in the context. A rejected draft parks and is not retried.
  - Drafts are cached per (canonical_key, company) and always go to /review. Approve or edit approves the draft.
  - It refuses SENSITIVE keys and any prompt containing a SENSITIVE value. `vault status` says tier 3 is
    disabled until the story pass is complete (currently 0 of 3).
- **Prompt 18 (email tracking, status timeline, interview prep page, funnel, digest): not started**, deferred by the user.

## Next steps
1. Log in: `autoapply browser-login naukri`, then `instahyre`, `unstop`, `internshala`. Then run
   `autoapply questions harvest --platform naukri --platform instahyre`.
   - Naukri is expected to report "apply is a submit" with 0 fields (see docs/NAUKRI_FORM.md).
2. Confirm the 3 story-bank answers at /intake, then install Ollama (`qwen3:8b`). That turns on tiers 2 and 3.
3. Run `autoapply companies brief --limit 100` for the companies behind in-policy jobs.
4. Run `autoapply apply` in review_only and work through /review.
   - Every success assertion stays provisional until a live submission is observed.
5. Prompt 18.

## Blocked on the user
- Logins:
  - Internshala is still not logged in (Google sign-in fails in Playwright). Use `autoapply browser-login internshala` in a plain browser.
  - Naukri and Instahyre also need logins.
- robots.txt decision: Internshala disallows `/student/*` and `/application/*`, and Unstop disallows `/api/*` and `/competitions/*/register`.
- Install Ollama (`qwen3:8b`) for answer tiers 2 and 3. Without it, both are skipped and those fields park.
- Story bank: 0 of 3 confirmed. Tier 3 stays off until all are confirmed at /intake.

## Known bugs / limits
- Himalayas is behind Cloudflare. Only the 25 jobs matched on a company ATS board have a route; 103 still have none.
- Some resolver targets time out from this network (Acko/Skillate). Urban Company is unresolved.
- Every success assertion is provisional until a live submission is observed.
  Unstop's steps after step 1 and its final control were not reachable logged out.
- The Naukri questionnaire chatbot is not answered automatically. Those applications end up `uncertain` for review.
- Instahyre yields almost no in-policy jobs.
- Further items are in docs/AUDIT.md (2.3 wrong ATS tokens, 2.5 no re-classification on richer sighting).

## Tests
- `.venv/bin/pytest -q`: 383 passed (2026-10-02).
- `tests/test_e2e.py` now runs the Greenhouse applier in a real headless Chromium against
  `tests/mock_ats.html`, offline, using an in-memory DB.
