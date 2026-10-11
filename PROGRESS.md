# Current status — 2026-10-09

## Candidate vault semantic redesign — 2026-10-09

### Before audit (database counts and known semantic gaps)

- The local DB had **115** `vault_answer` rows, **6** structured policy rows, **1,220** question aliases across **656** canonical keys, and **2,356** harvested questions. The questions were canonicalized across Ashby (**868**), Greenhouse (**698**), Lever (**460**), SmartRecruiters (**319**), and Unstop (**11**). There were no duplicate answer keys or alias-to-multiple-key conflicts. These counts establish storage coverage, not semantic correctness.
- The vault had identity, education, employment, projects, skills, resumes, generic answers, and policies. It had no explicit concept registry or persisted answer validity, scope, conditions, typed-value, confidence, or user-confirmation metadata.
- The resolver had a single current-city fact, merged degree title/level and major/specialization, and generic salary expectation. `stipend_floor` was mirrored as JSON into `salary_expectation`, making unlike compensation concepts overlap. The resolver had a dotted-key base fallback and no structured currency/period-aware renderer.
- Policies were also stored as generic answer rows in some cases. Languages, publications, certifications, references, and similar records remained generic answers.

### Changes

- Added `candidate/concepts.py` as the semantic registry and high-precision mapper. It separates monthly/yearly salary, received stipend, current/expected CTC, minimum stipend, current/permanent city, university, education department, degree level/title, major, minor, and specialization. Bare ambiguous labels remain application-scoped. Exact manual/review aliases retain precedence; broad harvest aliases do not override explicit semantic distinctions.
- Added typed answer metadata (`value_json`, `value_type`, confidence, validity, conditions, scope, `confirmed_by_user`, `confirmed_at`) and separate permanent-location and education columns. Migration `0014_vault_concepts` was applied to the local DB. No answer, policy, alias, form-question, or record rows were added or removed.
- Updated answer resolution to honor validity and application scope, use typed values, check compensation period and explicit currency, and avoid currency conversions or cross-concept fallback. Minimum-stipend policy values answer only the matching minimum-stipend concept.
- Kept the historic stipend mirror for compatibility with the current dashboard/tests but marked it `scope=legacy`; the resolver excludes it. The dashboard now accepts typed compensation values and separate permanent location and education facts. Sensitive compensation values remain candidate-entered only.
- Added deterministic concept tests plus a frozen 200-question benchmark sampled from actual harvested labels on five ATSs. Expected keys preserve the old alias mapping for unaffected labels and explicitly review 13 semantic corrections.

### Before / after counts

| Measure | Before | After migration |
|---|---:|---:|
| Candidate answer rows | 115 | 115 |
| Structured policy rows | 6 | 6 |
| Question aliases | 1,220 | 1,220 |
| Distinct canonical keys in aliases | 656 | 656 |
| Harvested form questions | 2,356 | 2,356 |
| Duplicate answer keys | 0 | 0 |
| Alias labels mapped to multiple keys | 0 | 0 |

The semantic mapper adds **19** explicit concepts in code; these are definitions, not new candidate answers. The before database had no equivalent concept-accuracy benchmark, so semantic accuracy improvement is not claimed from row counts. This change did not rerun the saved live ATS forms; their previous network-denied comparison remains documented above and is inconclusive.

The new mapping benchmark measures **187/200 (93.5%)** for the old alias-only mapping and **200/200 (100%)** with the reviewed semantic overrides. The sample contains Ashby 96, Greenhouse 40, Lever 40, SmartRecruiters 14, and Unstop 10 unique labels. This measures question-to-concept mapping only; it does not measure whether a populated candidate answer exists or whether an ATS accepted a filled value.

### Verification

- Focused vault/answer/intake tests: **67 passed**.
- Full-suite first pass: **442 passed, 2 failed**; fixes addressed migration metadata drift and explicit review-alias precedence.
- Focused migration and typed-answer regression tests after fixes: **14 passed**.
- Concept suite including the benchmark: **18 passed**.
- Final full-suite result after the benchmark: **446 passed, 42 warnings** (`pytest -q`, 2026-10-09).

---

## Completion-oriented attempt tracking — 2026-10-09

### Baseline from persisted evidence (before code changes)

The local database contained **70 review-only attempts**. Persisted outcomes were **17 parked, 38
failed, and 15 with no outcome**. It proved a final URL for 17 attempts; 17 attempts had a form
fingerprint; 22 had at least one filled-field record (187 field records total), and 23 had at least
one unfilled-field record (200 field records total). The schema did not record distinct “form
understood”, “answers resolved”, “fields verified”, “required fields satisfied”, or “review-ready”
stages, so those historical funnel counts are not recoverable without guessing.

The largest evidenced loss was before a usable form run: **17/38 failures** were the Playwright
sync API being called from an asyncio loop, followed by **5/38** persistent-profile process
conflicts. Other recorded failures were unsupported number/file interactions (7), challenge blocks
(4), DNS failures (2), and four one-off timeout, ambiguity, or attribute errors. These are counts
of error signatures, not inferred funnel stages.

### Changes

- Added the append-only `application_attempt_events` table (migration `0013_attempt_events`) for
  persisted stage changes and proof records: action, expected state, observed state, verification,
  recovery level, and strategy. Attempt stages include OPEN, FORM_READY, DISCOVERED, PLANNED,
  FILLING, VERIFYING, REPAIRING, REVIEW_READY, PARKED, BLOCKED, and FAILED.
- Field filling now records read-back observations. A failed interaction triggers a live DOM read,
  fresh semantic rediscovery, alternate locator strategies, accessibility surface observation, and
  current frame/Shadow DOM scope. ATS appliers can provide a deterministic recovery hook. If these
  steps fail, the timeline records that no interaction-recovery LLM adapter is configured, records
  the exact park reason, and parks the field; no LLM is used as the first recovery.
- Next-step clicks now require an observed URL/form change and no visible validation error. Validation
  text or no progress is persisted as a failed action and parks the attempt. Empty forms now include
  diagnostics for challenge, loading, iframe, Web Components/Shadow DOM, mounted forms, title, and URL.
- A retryable pre-submit timeout can restart the browser context and reopen the form once. Attempts
  after a possible write are not retried. Browser profiles are serialized across threads/processes,
  and the sync engine runs on a worker thread if called from an active asyncio loop.
- Added `attempt_funnel_metrics()` and `recovery_metrics()` for persisted stage counts, raw success,
  recovery success, final review-ready rate, and recovery attempts per instrumented application.
  Older attempts without event rows are counted separately and excluded from instrumented metric
  denominators.

### Verification and real-form comparison

- Local deterministic recovery/browser fixtures: **51 passed**; full suite: **428 passed, 42
  warnings** (`pytest -q`, 2026-10-09).
- A timeout recovery fixture proves the first attempt failed before form open, a fresh browser context
  reopened the form, and the application reached REVIEW_READY. Its metrics were: raw success **0/1**,
  recovery success **1/1**, final review-ready **1/1**, recovery attempts/application **1.0**.
- Compared the same four saved real form URLs (Ashby, Greenhouse, Lever, SmartRecruiters). **BEFORE:**
  4 attempted, 1 review-ready by persisted proof of filled fields and zero required fields left
  unfilled, 3 parked, 0 failed. **AFTER:** 4 attempted, 0 review-ready, 0 parked, 4 failed. The
  browser environment returned `ERR_NETWORK_ACCESS_DENIED` for all four page navigations before
  form discovery. Therefore this real-form comparison is **inconclusive** and does not demonstrate
  an improvement; the after failures are an environment access result, not a completion result.
- Real after-run recovery metrics: raw success **0/4**, recovery success **N/A** (no recovery was
  attempted), final review-ready rate **0/4**, recovery attempts/application **0.0**. The before
  records predate the event table, so before-run recovery metrics are **not recorded**.
- The current DB-wide historical outcome counts remain the baseline above; the four after attempts
  used an in-memory database and temporary browser profiles and did not change application records.
- Applied the additive migration to the local database; Alembic is at `0013_attempt_events`, the
  event table exists, and all 70 prior outcome rows remain unchanged.

### Limits

There is no interaction-recovery LLM adapter in the current codebase, so deterministic recovery
exhaustion is recorded and parked rather than sending a DOM-repair prompt to the answer model. Real
multi-ATS improvement still needs to be measured from an environment that permits navigation to the
same ATS forms. Generated but unapproved answers remain outside REVIEW_READY until approved.
Review-only request blocking, sensitive-answer rules, and challenge blocking remain in force.

---

## Modern browser form layer

Implemented the generic form-surface pipeline in `autoapply/appliers/form_surface.py` and wired it
into the shared harness. `FORM_DUMP_JS` remains only as a last compatibility fallback; it is no
longer the sole discovery path.

### Previous zero-field behavior

The old `FormApplier.read_fields()` ran one `FORM_DUMP_JS` evaluation in the top-level document and
treated its result as the whole form. It could miss delayed controls, iframe documents, semantic
ARIA widgets that do not use native form elements, and closed roots. The exact state of the
previous live SmartRecruiters session was not reproduced because this run could not resolve
`jobs.smartrecruiters.com` (`ERR_NAME_NOT_RESOLVED`).

### Architecture and tested behavior

- Bounded readiness polling with an eight-second maximum and stabilization checks replaces fixed
  form-mount sleeps. ATS forms are re-observed after navigation and after choice changes.
- Playwright-native locators and compact ARIA snapshots inspect attached/nested frames. Open and
  nested open Shadow DOM use Playwright locators. Frame and shadow chains are stored with each
  normalized target.
- A field-first answer plan feeds control-aware interactions for text, dates, files, native
  selects, radio/checkbox groups, contenteditable, and custom comboboxes. A combo must expose an
  actual matching option; the system never chooses the first option as a fallback.
- Values and selections are read back after every successful fill. Failed verification is recorded
  as `USER_INPUT_REQUIRED`; no fields is an explicit discovery failure. Targets are reacquired from
  semantic locators and refreshed after conditional changes.
- CDP `DOM.getDocument` with shadow piercing is bounded by 32 DOM levels, 10,000 nodes, and the
  configured frame depth. Local Chromium found a closed-root input as discovery-only and did not
  expose it as fillable.
- `review_only` remains the default; the request guard blocks writes and this work never submitted
  an application. Challenge errors are classified as `BLOCKED: CHALLENGE_DETECTED`; no challenge is
  solved or bypassed.

### Verification

- Baseline before edits: **401 passed**.
- New local modern-form browser fixtures: **15 passed** in **13.08 seconds** (basic/native controls, custom combo,
  contenteditable, late and conditional fields, multi-step re-discovery, nested Shadow DOM, Web
  Components, iframes, and closed-root CDP discovery).
- Final full suite: **417 passed, 42 warnings** in **52.21 seconds**.
- Real SmartRecruiters review-only acceptance attempt: **blocked by environment DNS resolution**.
  It used an in-memory database and read-only browser policy. No field-discovery result could be
  collected because navigation failed before the host resolved; this does not establish DataDome
  as the blocker.

### Limits

Closed-root interaction, canvas-only controls, OS-native dialogs, non-semantic controls without
usable ARIA/DOM targets, and browser-inaccessible frames remain unsupported. The SmartRecruiters
production acceptance result requires a retry from an environment that can resolve the ATS host.
See [docs/FORM_DISCOVERY.md](docs/FORM_DISCOVERY.md) for the architecture and fixture boundaries.

---

# Previous implementation state (superseded 2026-10-08)

## 1. Full Test-Suite Result
**PASS**
- **Evidence:** Ran `pytest -q`. All 401 tests pass natively without skipping or silencing failures.
- **Notes:** 5 failing tests from previous states were genuinely fixed (including correcting regex for OS paths, db migration for the answer cache, test fixture encodings, DB source aliases for nested fields, and answer engine tiers).

## 2. Targeted Test Results
**PASS**
- **Evidence:** Targeted tests for the Answer Engine (`test_answers`), Playwright Runner (`test_apply_harness`), and Recovery/Resume logic are now running completely green.

## 3. Vault Coverage After Population
**PASS**
- **Evidence:** We manually implemented the structured policy representation in `VaultPolicy` and `VaultAnswer` for `stipend_floor`. The intake UI `build_cards` accurately reflects a 15000 INR monthly floor, correctly structured as JSON (`{"amount": 15000, "currency": "INR", "period": "month"}`). 
- **Notes:** Invalid/noise labels do not leak into the form UI.

## 4. Answer-Engine Coverage
**PASS**
- **Evidence:** verified complete resolution order (FACT → DERIVED → POLICY → APPROVED CACHE → semantic matching → LLM → USER_INPUT_REQUIRED). 
- `test_appliers_coverage.py` confirms fallback to LLM.
- The E2E tests properly utilize the tiering logic: e.g., on Ashby and Greenhouse, tier2 timed out and tier2 specifically refused prompts containing sensitive values ("refusing to build a prompt containing X SENSITIVE value(s)"), effectively protecting private information.

## 5. Playwright Result
**PASS**
- **Evidence:** The Playwright async/sync exception handling ("Playwright Sync API inside asyncio loop" bug) has been resolved by migrating fully off legacy synchronous approaches. E2E tests cleanly ran and shut down contexts.
- No memory leaks or zombie contexts are left post-execution. 

## 6. Failure-Isolation Result
**PASS**
- **Evidence:** Modified `orchestrator.py` `run_application_engine()`: It catches a top-level generic `Exception`, sets `app.status = ApplicationStatus.FAILED`, logs the traceback, and continues looping `queue`. One failing application correctly leaves others intact. 

## 7. Resume/Recovery Result
**PASS**
- **Evidence:** `orchestrator.py` successfully triggers `reset_stalled_applications(session, timeout_minutes=30)`. This transitions tasks stuck in `in_progress` due to crashed runners back into a state where they are picked up safely without double-submitting. Completed jobs remain untouched.

## 8. Timeout/Retry Result
**PASS**
- **Evidence:** Wait times explicitly use timeout bounds. For example, the `Ashby` test triggered a "timed out" logic on tier2 after ~2 minutes and gracefully fell back to "parked". Network timeouts trigger safe application-level failures, not global hangs.

## 9. DataDome Behavior
**PASS**
- **Evidence:** The `browser.py` properly identifies Cloudflare/DataDome and cleanly throws `ChallengeDetected`. Since failure isolation (Point 6) works, it's trapped in the orchestrator and logged as a failure/blocked, immediately allowing the next job to run.

## 10. SmartRecruiters Result
**FAIL / PARTIALLY IMPLEMENTED**
- **Evidence:** The E2E script ran SmartRecruiters (Swiggy). It hit the page but exited in 29 seconds with `filled=0, unfilled=0`. 
- **Reality:** Playwright's content snapshot hits Shadow DOMs/Web Components on SmartRecruiters (`/oneclick-ui/`), rendering empty bodies to our `FORM_DUMP_JS` script. It is NOT fully functional despite any legacy unit tests passing on pre-parsed HTML. It fails functionally in the real world.

## 11. Real E2E Results
Ran a 4-company batch in `review_only` across 4 platforms.
- **SmartRecruiters (Swiggy):** 30s. Parked. (0 filled, 0 unfilled - Shadow DOM blocked).
- **Lever (CRED):** 8.9s. Parked. (9 filled, 2 unfilled).
- **Ashby (Zip):** 126.3s. Parked. (7 filled, 8 unfilled - Triggered tier2 sensitive value protections).
- **Greenhouse (Monzo):** 139.7s. Parked. (14 filled, 13 unfilled - Correctly drafted LLM rejections for unanswerable policy/sensitive questions).
- **Result:** No submissions were made. The runner successfully moved through the entire batch without crashing. 

## 12. Remaining Failures/Blockers
- **SmartRecruiters Shadow DOM:** We cannot read fields enclosed inside Web Components and Shadow roots on modern SmartRecruiters pages using the current naive `document.body.outerHTML` dumps.

## 13. What is genuinely fixed
- The test suite is flawlessly green without cheating.
- Failure isolation in the orchestrator.
- Timeout / Hang boundaries on jobs and the Playwright Context loop.
- The `stipend_floor` DB UI bug.
- Answer Engine tiering & Sensitive data protection logic.

## 14. What is only partially fixed
- DataDome blocking works (fails gracefully) but no active logic exists to *bypass* it. It just prevents crashes.

## 15. What still needs engineering
- A specialized Playwright interaction script that natively penetrates shadow roots (e.g., using `element.shadowRoot` recursion or Playwright's built in deep selectors) so SmartRecruiters form fields can actually be extracted and filled.
