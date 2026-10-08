# PROGRESS.md

Single source of truth for project state. Last updated 2026-10-07. Branch `main`; check
`git log -1` and `git status` for the exact commit and any uncommitted work.

## Done (prompts 1–17)
- **Foundation:** config (`config/*.yaml`), location policy, Alembic migrations 0001–0011, company-first data model,
  dedup v2, data-corruption fixes.
- **Discovery:**
  - HTTP sources: Unstop, Internshala, Instahyre, Himalayas, Adzuna, LinkedIn guest, `ats_boards`.
  - Browser tier: Naukri, Wellfound.
  - Politeness budgets and `source_runs`.
  - RemoteOK, Remotive, Arbeitnow and YC WaaS are disabled.
- **Company universe and ATS resolver:**
  - About 5.7k companies.
  - The resolver probes require at least one open job.

## Recently Completed (Production Fixes & Test Suite)
- **Strict Typed Answer Layer**: Implemented `autoapply.answers.type_adapter` and integrated it before `fill()` in `harness.py`. Date range dicts correctly map to target field types, boolean primitive mapping, etc. Wrong-type answers explicitly raise `TypeValidationError` / `ParkApplication` rather than crashing Playwright.
- **Robust Dropdown Option Matching**: Implemented exact -> normalized -> synonym -> fuzzy match hierarchy in `type_adapter`. `harness.py` dynamically builds valid UI options for comboboxes before selecting. Ambiguous mappings strictly return `USER_INPUT_REQUIRED` without hallucinating options.
- **Basic Vault Answers (Deterministic)**: Verified semantic question variants resolve robustly (via QuestionAlias).
- **Persistent Cache (ApprovedAnswerCache)**: Added lookup step directly in `AnswerEngine.resolve()` so previously user-approved generated answers are persisted and reused cleanly.
- **Real Active Resume logic**: Verified `resume_for()` selects the actual configured `.pdf` instead of a dummy value.
- **PhD-Only Filter logic**: Tested and verified the regex safely rejects strictly PhD internships while correctly preserving "Bachelors, Masters, or PhD" variants.
- **SmartRecruiters State Handling**: Expanded `next_control` to recognize "add information", "continue", "review". Strengthened `submit_control` to specifically target "submit" or "send application" to prevent confusing intermediate states with final submission.
- **DataDome Detection**: Added explicit transition to `FAILED` with `CHALLENGE_DETECTED` rather than hanging generic timeouts.
- **Automated Acceptance / Regression Test Suite**: Extensive automated regression test coverage written for type adapters, dropdown matcher, PhD filter, caching logic, and `harness.py` flow.
- **Candidate Knowledge Base**: Implemented `autoapply.answers.knowledge.CandidateKnowledgeBase` representing a 5-layer knowledge model (Facts, Derived Facts, Policies, Approved Answers, Story Bank). Integrated strict resolution order mapping into `AnswerEngine` tracking origin (`FACT`, `DERIVED`, `POLICY`, `USER_INPUT`, `APPROVED_CACHE`, `LLM`). SENSITIVE concepts correctly park missing answers. Generated a 250-question coverage benchmark report showing ~19.6% answerability against the candidate's existing vault without LLM generation.
- **Knowledge Base Expansion**: Expanded the canonical knowledge model to handle the 600-question target pool. Mapped 52 new canonical concepts (e.g., Work Auth, Compensation, Logistics) and 120+ semantic variants. Extended deterministic derivations (e.g., `experience.internship_count`, `experience.project_count`, `education.current.degree`). Generated a new comprehensive `coverage_report_expanded.md` and added regression tests for derived facts and sensitive field handling.
- Completed audit of the intake gap pass.
- Added classify_card logic in utoapply/candidate/catalog.py to prevent raw application-form questions from being treated as Candidate Vault intake fields.
- Deduplicated concepts and kept sensitive fields user-entered only.
- Mapped classification outputs ("none") through utoapply/candidate/intake.py so they are not surfaced to /intake.
- Added a regression test in 	ests/test_intake.py to prove raw labels like "Attach" cannot appear.
