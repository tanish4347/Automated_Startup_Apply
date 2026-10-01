# PROGRESS.md

Single source of truth for project state. Last updated 2026-10-01. Branch `main`; check
`git log -1` and `git status` for the exact commit and any uncommitted work.

## Done (prompts 1–15)
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

## In progress: prompt 16, "close the applier coverage gap"
Goal: harden the 4 ATS appliers on the harness, add Unstop and Naukri appliers, harvest Naukri and Instahyre, and produce a coverage report. Everything stays review_only.

**Groundwork committed in "wip: applier coverage groundwork" (tests pass, 356):**
- `harness.py`:
  - `Reroute` exception: the job is re-tagged via `resolve.reroute()`, the attempt outcome is `rerouted`, and the job is re-queued.
  - `FormApplier.click_is_submit`: review_only parks before the click and takes a screenshot.
  - Combobox filling.
- `models/attempt.py`: `REROUTED` outcome.
- `resolve.py`: `reroute()`. A target that is not an ATS becomes `company_site`.
- `questions/platform_forms.py`: `FORM_DUMP_JS` walks open shadow roots and reports `role=combobox`.

**Findings:**
- **Naukri: clicking Apply IS the submission.** The page JS binds a click on `.apply-button` to
  `POST /cloudgateway-workflow/workflow-services/apply-workflow/v1/apply` with the jobId. If the job has a
  questionnaire (`questionnaireIdPresent`), a chatbot (`botapi/v5/respond`) follows. 13 of the 18 in-policy jobs are
  `companyApplyJob=true` ("apply on company site") and should reroute.
  - Still to write: `docs/NAUKRI_FORM.md`.
- The Naukri browser profile is **not logged in**. Run `autoapply browser-login naukri`.
- Himalayas `applicationLink` always points back to himalayas.app, which is behind Cloudflare. Its 128 jobs have no route.
- Unstop raw data has no external-apply field. Detect it at apply time (the Register/Apply target host) and `Reroute`.
- Sample ATS job URLs exist in the DB (ids 2658 gh, 18221 ashby, 17166 lever, 20289 smartrecruiters) for read-only checks.

**Next steps (in order):**
1. Rewrite `appliers/greenhouse.py`, `lever.py`, `ashby.py`, `smartrecruiters.py` as `FormApplier`s:
   - Each needs URL building, `open_form`, `submit_control`, and an explicit `success_assertion`.
     - Greenhouse: `/confirmation` URL or "Thank you for applying". The old `.asterisk` error check is wrong: `.asterisk` is the required-field marker.
     - Lever: `/thanks`.
     - Ashby: success container.
     - SmartRecruiters: provisional.
   - Delete their bespoke logic and remove `tests/test_e2e.py`'s legacy usage.
   - Check each against one real form, read-only.
2. `appliers/unstop.py`:
   - Open the posting, then Register.
   - An external host means `Reroute`.
   - A login wall means `NeedsLogin`.
   - The success assertion is provisional.
3. `appliers/naukri.py`:
   - `click_is_submit=True`.
   - `#company-site-button` means `Reroute` to the popup URL.
4. Merge the registries: move the harness appliers into `registry.py` (keep the LinkedIn guard and its tests) and drop the legacy path in `orchestrator.py`.
5. Add an `autoapply appliers coverage` command: for each in-policy job, the handling applier, and the count with none.
6. After the user logs in: `autoapply questions harvest --platform naukri` and `--platform instahyre`.
7. Write `docs/NAUKRI_FORM.md` and add tests for all of the above.

## Blocked on the user
- Logins:
  - Internshala is still not logged in (Google sign-in fails in Playwright). Use `autoapply browser-login internshala` in a plain browser.
  - Naukri and Instahyre also need logins.
- robots.txt decision: Internshala disallows `/student/*` and `/application/*`, and Unstop disallows `/api/*` and `/competitions/*/register`.
- Install Ollama (`qwen3:8b`) for answer tier 2. Without it, tier 2 is skipped.

## Known bugs / limits
- Himalayas is behind Cloudflare, so there is no apply route for its 128 in-policy jobs.
- Some resolver targets time out from this network (Acko/Skillate). Urban Company is unresolved.
- Every success assertion is provisional until a live submission is observed.
- Instahyre yields almost no in-policy jobs.
- Further items are in docs/AUDIT.md (2.3 wrong ATS tokens, 2.5 no re-classification on richer sighting).

## Tests
- `.venv/bin/pytest -q`: 356 passed (2026-10-01).
- `tests/test_e2e.py` writes to the real DB, and `tests/test_career.py` hits live APIs.
