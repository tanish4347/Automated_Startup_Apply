# CLAUDE.md

Internship-application automation for one candidate (Tanish). Pipeline: discovery → storage →
form filling → application tracking → email → interview prep. Read **PROGRESS.md** (current state,
next steps) and **ARCHITECTURE.md** (how the code works) before changing anything.

## Scope
- Internships only, heavily Indian companies and startups.
- Location: remote is preferred; on-site/hybrid counts ONLY in Mumbai (incl. Navi Mumbai, Thane).
  Everything is stored; `jobs.location_fit` marks in-policy (`ok`). Report in-policy numbers, not totals.

## Hard rules (never break)
- **LinkedIn:** guest HTML only. Never automate anything on logged-in LinkedIn. There is no LinkedIn
  applier, ever (the registry refuses one; tests enforce it). Naukri and Wellfound may be scraped more aggressively.
- **Submission safety:** `submit_mode` in `config/apply.yaml` is `review_only` unless the user edits
  it to exactly `live`. No code may write that file or promote a platform. review_only runs the
  browser read-only (every non-GET is aborted).
- Never record `submitted` on "no exception thrown"; only the applier's explicit success assertion counts.
  Anything else is `uncertain`.
- Question harvests never submit: halt before the submit control and dump the form.
- **SENSITIVE fields** (`candidate/sensitive.py`): user-entered only, through `user_input()`. The answer
  engine may return them verbatim but never infer, reword or generate them. A test fails on any
  other write path.
- Never invent intake questions that are not in `docs/QUESTIONS.md` (the harvest).
- Respect robots.txt (`RobotsRules`, RFC 9309) and the politeness budgets in `config/politeness.yaml`.
  Challenges/CAPTCHAs are detected and never solved.
- Never double-apply (dedup-cluster claim in `application_claims`).

## Git / secrets
- Remote: `git@github-tanish:tanish4347/Automated_Startup_Apply.git` (repo-specific SSH host alias;
  do not change global git or GitHub credentials, since they belong to another account).
- Never print or hardcode tokens, keys or passwords. `.env` holds keys (Adzuna, GitHub, Gemini).
- No force-push, reset or rebase of the user's branch. Commit or push only when asked.

## Coding rules
- Python 3.12, SQLAlchemy 2 + SQLite (WAL), Alembic migrations in `autoapply/migrations/versions/`
  (add a new numbered migration for schema changes; never edit applied ones).
- New appliers subclass `appliers/harness.FormApplier` (open_form, submit_control,
  success_assertion) and get answers from `answers/engine.harness_answerer`. No bespoke submit logic.
- Tests: `.venv/bin/pytest -q` (offline, uses recorded fixtures); keep it green. Add a test for
  every safety rule you touch.
- Match the surrounding style: module docstrings record endpoints and decisions found by probing.
- Keep PROGRESS.md current at the end of each piece of work.
