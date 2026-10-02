"""Command-line interface for AutoApply."""

from __future__ import annotations

import argparse
import sys

if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from autoapply.config import get_settings
from autoapply.logging import setup_logging, get_logger
from autoapply.models import engine_from_settings, get_session_factory


def cmd_init(args: argparse.Namespace) -> None:
    """Create the database or migrate it to the latest schema."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    log = get_logger("cli.init")

    from autoapply.models.migrate import upgrade_db
    upgrade_db(settings.db.url)
    log.info("database_initialized", url=settings.db.url)
    print(f"Database initialized at: {settings.db.url}")


def cmd_status(args: argparse.Namespace) -> None:
    """Show system status."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)

    engine = engine_from_settings(settings.db.url, settings.db.echo)
    SessionFactory = get_session_factory(engine)

    with SessionFactory() as session:
        from autoapply.services.job_service import count_jobs
        from autoapply.services.application_service import get_application_stats

        job_count = count_jobs(session)
        app_stats = get_application_stats(session)

        print(f"\n=== AutoApply Status ===")
        print(f"Total active jobs: {job_count}")
        print(f"\nApplication stats:")
        for status, count in app_stats.items():
            print(f"  {status}: {count}")
        print()


def cmd_dashboard(args: argparse.Namespace) -> None:
    """Start the dashboard web server."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)

    # Lazy import to avoid loading uvicorn unless needed
    import uvicorn
    from autoapply.dashboard.app import create_app

    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.dashboard.host,
        port=settings.dashboard.port,
    )


def cmd_discover(args: argparse.Namespace) -> None:
    """Run job discovery orchestrator."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    if args.browser:
        from autoapply.sources.orchestrator import run_browser_discovery
        print("Starting browser discovery (each source in its own process)...")
        outcomes = run_browser_discovery(only=args.source, force=args.force, headless=not args.headed)
        print("\nBrowser discovery complete.\n")
        for name, outcome in outcomes.items():
            print(f"  {name:<12} {outcome}")
        return
    from autoapply.sources.orchestrator import run_discovery
    print("Starting discovery...")
    stats = run_discovery(only=args.source)
    print("\nDiscovery complete.\n")
    print(f"{'source':<14}{'fetched':>9}{'new':>7}{'merged':>8}{'accepted':>10}{'failed':>8}")
    for name, c in stats.items():
        print(f"{name:<14}{c['fetched']:>9}{c['new']:>7}{c['merged']:>8}{c['accepted']:>10}{c['failed']:>8}")


LOGIN_URLS = {
    "naukri": "https://www.naukri.com/nlogin/login",
    "internshala": "https://internshala.com/login/user",
    "unstop": "https://unstop.com/auth/login",
    "instahyre": "https://www.instahyre.com/login/",
    "wellfound": "https://wellfound.com/login",
    "yc_waas": "https://www.workatastartup.com/",
}


def cmd_browser_login(args: argparse.Namespace) -> None:
    """Open a platform's persistent browser profile for a one-time manual login or challenge.

    Default: a plain browser process (the same Chromium the harvester uses, same profile folder)
    with no automation attached. Google refuses sign-in in automation-controlled browsers
    ("This browser or app may not be secure"), so "Continue with Google" works only this way.
    The session cookies stay in the profile and the automated runs reuse them.
    --record: the old mode, driven by Playwright, which also logs the site's XHR calls to
    data/browser_profiles/<platform>.calls.jsonl (for reverse-engineering; Google login fails there).
    """
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    from autoapply.sources.browser import PROFILE_ROOT, BrowserSession, assert_platform_allowed
    assert_platform_allowed(args.platform)
    if args.platform not in LOGIN_URLS:
        sys.exit(f"Unknown platform {args.platform!r}. Choose from: {', '.join(LOGIN_URLS)}")
    url = LOGIN_URLS[args.platform]
    if args.record:
        with BrowserSession(args.platform, headless=False) as s:
            s.page.goto(url, wait_until="domcontentloaded")
            print(f"A browser window is open on {url}. Sign in by hand, then close the window.")
            s.page.wait_for_event("close", timeout=0)
        print(f"Saved. Profile: {s.profile_dir}\nCalls the site made: {s.calls_path}")
        return
    import subprocess
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        chromium = p.chromium.executable_path   # the binary the harvester runs: profile stays compatible
    profile = PROFILE_ROOT / args.platform
    profile.mkdir(parents=True, exist_ok=True)
    print(f"Opening a normal browser window on {url}\n"
          "Sign in by hand (\"Continue with Google\" works here), open any job once so the site sets its\n"
          "cookies, then close the whole window. Nothing is automated while it is open.")
    subprocess.run([chromium, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                    "--password-store=basic", url], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Saved. Profile: {profile}\nNow run e.g. `autoapply questions harvest --platform {args.platform}`.")

def cmd_apply(args: argparse.Namespace) -> None:
    """Run application engine."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    from autoapply.appliers.orchestrator import run_application_engine
    print(f"Starting application engine (limit: {args.limit})...")
    run_application_engine(limit=args.limit)
    print("Application engine complete.")


def cmd_ats(args: argparse.Namespace) -> None:
    """Resolve companies' ATS (slow cadence, separate from job fetching), or report on it."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    from autoapply.discovery.ats_resolver import ats_stats, run_resolver
    engine = engine_from_settings(settings.db.url, settings.db.echo)
    with get_session_factory(engine)() as session:
        if args.action == "resolve":
            stats = run_resolver(session, args.limit, allow_probe=not args.no_probe, names=args.company)
            print("Resolved this run: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
        elif args.action == "resolve-browser":
            from autoapply.discovery.ats_resolver import browser_stage
            stats = browser_stage(session, args.limit, headless=not args.headed)
            print("Browser stage: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
        st = ats_stats(session)
        print(f"\nCompanies: {st['companies']}  resolved: {st['resolved']} (India: {st['resolved_india']})  "
              f"never tried: {st['never_tried']}  backing off: {st['failing']}  generic crawl: {st['generic_crawl']}")
        print(f"\n{'platform':<16}{'companies':>10}{'india':>8}")
        for ats, (n, india) in st["by_platform"].items():
            print(f"{ats:<16}{n:>10}{india:>8}")


def cmd_answers(args: argparse.Namespace) -> None:
    """Answer engine: seed aliases, or explain how a job's form would be answered (fills nothing)."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    from autoapply.answers.engine import explain, seed_aliases
    engine = engine_from_settings(settings.db.url, settings.db.echo)
    with get_session_factory(engine)() as session:
        if args.action == "seed":
            print("Aliases added:", seed_aliases(session))
            return
        from autoapply.models.job import Job
        job = session.get(Job, args.job_id) if args.job_id else None
        if job is None:
            sys.exit("usage: autoapply answers explain --job-id N [--harvested]")
        fields = _form_fields_for(session, job, harvested=args.harvested)
        rows = explain(session, fields)
        print(f"\n{job.title} at {job.company} ({job.apply_channel}) - {len(rows)} fields, nothing filled\n")
        print(f"{'tier':<8}{'conf':>6}  {'canonical_key':<34}{'field':<46}value / reason")
        for r in rows:
            shown = r["value"] if r["value"] is not None else f"[{r['reason']}]"
            print(f"{r['tier'] or '-':<8}{r['confidence']:>6.2f}  {(r['key'] or '-')[:33]:<34}{r['label'][:45]:<46}{str(shown)[:70]}")
        tiers = __import__("collections").Counter(r["tier"] for r in rows)
        print("\nper tier: " + ", ".join(f"{k}={v}" for k, v in tiers.most_common()))


def _form_fields_for(session, job, harvested: bool = False):
    """The job's form fields: live from its applier (read-only browser), or the harvested ones."""
    from autoapply.models.form_question import FormQuestion
    if not harvested:
        from autoapply.appliers.registry import find_applier, setup_appliers
        setup_appliers()
        applier = find_applier(job)
        if applier is not None:
            from autoapply.sources.browser import BrowserSession
            with BrowserSession(applier.platform) as bs:
                bs.read_only(applier.read_only_allow) if applier.read_only_allow else bs.read_only()
                applier.open_form(bs, job)
                return [(f.label, f.field_type, f.options) for f in applier.read_fields(bs)]
        print(f"No harness applier for apply_channel={job.apply_channel!r}; using the harvested questions.")
    platform = job.apply_channel if job.apply_channel not in (None, "ats_direct", "unknown") else (job.ats_platform or "")
    rows = session.query(FormQuestion).filter(FormQuestion.platform == platform).order_by(FormQuestion.id).all()
    seen, out = set(), []
    for r in rows:
        if r.raw_label not in seen:
            seen.add(r.raw_label)
            out.append((r.raw_label, r.field_type, r.options_json))
    return out


def cmd_vault(args: argparse.Namespace) -> None:
    """The candidate Vault: seed the question catalog, import a CV, report coverage."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    from autoapply.candidate.catalog import seed_catalog
    from autoapply.candidate.intake import coverage
    engine = engine_from_settings(settings.db.url, settings.db.echo)
    with get_session_factory(engine)() as session:
        if args.action == "seed":
            st = seed_catalog(session)
            print(f"Catalog from docs/QUESTIONS.md: {st['seeded']} new, {st['kept']} kept, {st['sensitive']} SENSITIVE")
        elif args.action == "import-cv":
            if not args.path:
                sys.exit("usage: autoapply vault import-cv PATH")
            from autoapply.candidate.cv_parser import import_cv
            seed_catalog(session)
            st = import_cv(session, args.path)
            print("Imported from CV: " + ", ".join(f"{k}={v}" for k, v in st.items()) +
                  "\nEverything is NEEDS REVIEW: confirm it at /intake (autoapply dashboard).")
        cov = coverage(session)
        pct = 100 * cov["answered"] / cov["total"] if cov["total"] else 0
        print(f"\nVault: {cov['answered']} of {cov['total']} questions confirmed ({pct:.0f}%); "
              f"{cov['prefilled']} pre-filled from the CV, waiting for you to confirm")
        print(f"\n{'category':<14}{'confirmed':>10}{'total':>7}")
        for cat, (done, total) in cov["by_category"].items():
            print(f"{cat:<14}{done:>10}{total:>7}")
        from autoapply.answers.generate import tier3_status
        on, why = tier3_status(session)
        print(f"\nGenerated answers: {why[0].upper() + why[1:]}."
              + ("" if on else "\n  Until then, free-text questions (why this company, tell us about a project) park for you."))
        print(f"\nPolicy rules set: {', '.join(cov['policy_set']) or 'none'}")
        if cov["policy_missing"]:
            print(f"Policy rules missing: {', '.join(cov['policy_missing'])}")
        if cov["blank_sensitive"]:
            print(f"\nSENSITIVE and still blank ({len(cov['blank_sensitive'])}): applications that ask these are parked")
            for key in cov["blank_sensitive"]:
                print(f"  - {key}")


def cmd_questions(args: argparse.Namespace) -> None:
    """Harvest application-form questions from real forms (never submitting), or write the report."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    import asyncio
    from pathlib import Path
    from autoapply.questions import cluster, harvest, platform_forms
    engine = engine_from_settings(settings.db.url, settings.db.echo)
    notes_path = Path("data/questions_harvest_notes.txt")
    with get_session_factory(engine)() as session:
        if args.action == "dump-form":
            from autoapply.appliers.internshala import NeedsLogin
            try:
                _dump_form(session, args.platform[0] if args.platform else "internshala", args.job_id)
            except NeedsLogin as e:
                sys.exit(f"Not dumped: {e}")
            return
        if args.action == "harvest":
            wanted = args.platform or ["greenhouse", "ashby", "lever", "smartrecruiters",
                                       "internshala", "unstop", "naukri", "instahyre"]
            notes: list[str] = []
            for plat in wanted:
                try:
                    if plat in harvest.ATS_HARVEST:
                        tokens = harvest.sample_companies(session, plat, args.cap)
                        forms = asyncio.run(harvest.harvest_ats(plat, tokens))
                    elif plat == "smartrecruiters":
                        forms = platform_forms.harvest_smartrecruiters(harvest.sample_companies(session, plat, args.cap))
                    else:
                        forms = [platform_forms.harvest_platform(session, plat)]
                    n = harvest.store_forms(session, forms)
                    msg = f"{plat}: {len(forms)} forms from {len({f.company_key for f in forms})} companies, {n} fields"
                except platform_forms.NeedsLogin as e:
                    msg = f"{plat}: NOT HARVESTED - {e}"
                except Exception as e:
                    msg = f"{plat}: NOT HARVESTED - {type(e).__name__}: {e}"
                print(msg)
                notes.append(msg)
            notes_path.parent.mkdir(parents=True, exist_ok=True)
            notes_path.write_text("\n".join(notes) + "\n")
        clusters, weights, counts, sampled = cluster.build(session)
        notes = notes_path.read_text().splitlines() if notes_path.exists() else []
        Path("docs").mkdir(exist_ok=True)
        Path("docs/QUESTIONS.md").write_text(cluster.render(clusters, weights, counts, sampled, notes))
        print(f"\n{len(clusters)} clusters ({sum(1 for c in clusters if c.reasons)} to review) -> docs/QUESTIONS.md")


def _dump_form(session, platform: str, job_id: int | None) -> None:
    """Open one real application form read-only, dump every field, feed the question harvest,
    and write docs/<PLATFORM>_FORM.md. Submits nothing."""
    from pathlib import Path
    from autoapply.appliers.registry import setup_appliers
    from autoapply.models.job import Job
    from autoapply.questions.harvest import HarvestedField, HarvestedForm, store_forms
    from autoapply.sources.browser import BrowserSession, detect_challenge
    applier = next(a for a in setup_appliers() if a.platform == platform)
    job = session.get(Job, job_id) if job_id else (session.query(Job).filter(Job.apply_channel == platform,
                                                   Job.is_active == 1, Job.location_fit == "ok").order_by(Job.id).first())
    with BrowserSession(platform) as bs:
        bs.read_only(applier.read_only_allow) if applier.read_only_allow else bs.read_only()
        applier.open_form(bs, job)
        fields = applier.read_fields(bs)
        html = bs.page.content()
        captcha = detect_challenge(html=html) or ("recaptcha/grecaptcha present" if "grecaptcha" in html or
                                                   "g-recaptcha" in html else None)
        url, blocked = bs.page.url, list(bs.blocked)
    store_forms(session, [HarvestedForm(platform, platform, job.company, url,
                                        [HarvestedField(f.label, f.field_type, f.required, f.options, "platform") for f in fields],
                                        job_id=job.id)])
    lines = [f"# {platform.title()} application form", "",
             f"Dumped read-only by `autoapply questions dump-form --platform {platform}`: nothing was filled or submitted.", "",
             f"- Job: {job.title} at {job.company} (job id {job.id})", f"- Form URL: {url}",
             f"- CAPTCHA / challenge: {captcha or 'none seen'}",
             f"- Requests the read-only guard blocked while loading: {len(blocked)}", "",
             "| # | label | type | required | options |", "|---:|---|---|---|---|"]
    for i, f in enumerate(fields, 1):
        opts = "; ".join(f.options or [])[:200]
        lines.append(f"| {i} | {f.label.replace('|', '/')} | {f.field_type} | {'yes' if f.required else 'no'} | {opts.replace('|', '/')} |")
    out = Path("docs") / f"{platform.upper()}_FORM.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"{len(fields)} fields -> {out} (and the question harvest)")


def cmd_appliers(args: argparse.Namespace) -> None:
    """Appliers: the registry, link resolution, and per-job coverage of the in-policy pool."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    from autoapply.appliers import registry, resolve
    engine = engine_from_settings(settings.db.url, settings.db.echo)
    with get_session_factory(engine)() as session:
        if args.action == "list":
            from autoapply.appliers.harness import load_config, submit_mode
            for a in registry.setup_appliers():
                print(f"{a.platform:<16} channels={','.join(a.channels):<14} mode={submit_mode(a.platform, load_config())}"
                      f"{'  (apply click is the submission)' if a.click_is_submit else ''}")
            return
        if args.action == "resolve":
            print(f"Re-tagged without a request (stored link already off-platform): {resolve.reroute_known(session)}")
            res = resolve.run_resolver(session, sources=("unstop",))
            print(f"Unstop resolved natively: {res['outcomes']}")
            res = resolve.resolve_himalayas_boards(session, limit=args.limit)
            print(f"Himalayas via company ATS boards: {res['outcomes']}")
        cov = registry.coverage(session)
        print(f"\nIn-policy active jobs: {cov['total']}   handled: {cov['total'] - cov['none']}   no applier: {cov['none']}")
        print(f"\n{'applier':<18}{'jobs':>6}")
        for name, n in cov["by_applier"].items():
            print(f"{name:<18}{n:>6}")
        if cov["none_by_channel"]:
            print("\nNo applier, by apply_channel: " + ", ".join(f"{k}={v}" for k, v in cov["none_by_channel"].items()))
        print(f"\n{'source':<14}handled by")
        for src, c in cov["by_source"].items():
            print(f"{src:<14}" + ", ".join(f"{k}={v}" for k, v in c.items()))
        if args.jobs:
            print(f"\n{'job':>7}  {'source':<12}{'channel':<14}{'ats':<16}applier")
            for jid, src, ch, ats, name in cov["rows"]:
                print(f"{jid:>7}  {src:<12}{(ch or '-'):<14}{(ats or '-'):<16}{name or '(none)'}")


def cmd_companies(args: argparse.Namespace) -> None:
    """Company briefs: fetched once per company from its own site, cached permanently."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    from autoapply.models.company import Company
    from autoapply.models.job import Job
    from autoapply.services.company_brief import brief_for, summary
    engine = engine_from_settings(settings.db.url, settings.db.echo)
    with get_session_factory(engine)() as session:
        q = session.query(Company)
        if args.company:
            q = q.filter(Company.name.ilike(f"%{args.company}%"))
        else:   # companies behind in-policy jobs, without a brief yet
            ids = {r[0] for r in session.query(Job.company_id).filter(Job.is_active == 1, Job.location_fit == "ok",
                                                                      Job.company_id.isnot(None))}
            q = q.filter(Company.id.in_(ids))
            if not args.refresh:
                q = q.filter(Company.company_brief_at.is_(None))
        done = 0
        for c in q.order_by(Company.priority.desc(), Company.id).limit(args.limit):
            b = brief_for(session, c, refresh=args.refresh)
            if b is None:
                print(f"{c.name}: not fetched (daily budget used up?)")
                break
            s = summary(b)
            done += 1
            print(f"{c.name}: {len(s['sources'])} page(s){'  [' + s['fetch_error'][:80] + ']' if s.get('fetch_error') else ''}")
            if args.company:
                for k in ("what", "product", "stage", "size", "founded", "location"):
                    if s.get(k):
                        print(f"  {k:<9}{s[k][:160] if isinstance(s[k], str) else s[k]}")
                for line in s["notable"][:4]:
                    print(f"  - {line[:160]}")
        print(f"\nBriefs built this run: {done}")


def cmd_universe(args: argparse.Namespace) -> None:
    """Seed the company universe, or report on it."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    from autoapply.discovery.universe import run_universe, universe_stats
    from autoapply.models.company import CompanyAlias, Company
    engine = engine_from_settings(settings.db.url, settings.db.echo)
    with get_session_factory(engine)() as session:
        if args.action == "seed":
            results = run_universe(session, only=args.source)
            print(f"\n{'seeder':<14}{'seen':>8}{'created':>9}{'merged':>8}{'existing':>10}{'india+':>8}")
            for name, st in results.items():
                print(f"{name:<14}{st.seen:>8}{st.created:>9}{st.merged:>8}{st.existing:>10}{st.india_created:>8}")
            print()
        if args.action in ("seed", "stats"):
            st = universe_stats(session)
            print(f"Companies: {st['total']}  India: {st['india']}  with website: {st['with_website']}  "
                  f"ATS resolved: {st['ats_resolved']}  merge aliases: {st['aliases']}")
            print(f"\n{'seed_source':<16}{'companies':>10}{'india':>8}")
            for src, n in st["by_seed_source"].items():
                print(f"{src:<16}{n:>10}{st['india_by_seed_source'].get(src, 0):>8}")
        if args.action == "merges":
            rows = (session.query(CompanyAlias, Company).join(Company, CompanyAlias.company_id == Company.id)
                    .order_by(CompanyAlias.id.desc()).limit(args.limit).all())
            for alias, company in rows:
                print(f"{alias.match_type:<7} {alias.alias!r:<40} -> {company.name!r:<40} "
                      f"[{alias.seed_source}] {alias.evidence or ''}")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="autoapply",
        description="Automated job application system",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # init
    sub.add_parser("init", help="Initialize the database")

    # status
    sub.add_parser("status", help="Show system status")

    # dashboard
    sub.add_parser("dashboard", help="Start the dashboard")

    # discover
    disc = sub.add_parser("discover", help="Run job discovery sources")
    disc.add_argument("--source", action="append",
                      help="Run only this source (repeatable), e.g. --source unstop --source internshala")
    disc.add_argument("--browser", action="store_true",
                      help="Run the browser tier (naukri, wellfound, yc_waas) instead of the HTTP sources")
    disc.add_argument("--force", action="store_true", help="--browser: ignore the per-source cadence")
    disc.add_argument("--headed", action="store_true", help="--browser: show the browser windows")

    an = sub.add_parser("answers", help="Answer engine: seed aliases, or explain a job's form mapping (fills nothing)")
    an.add_argument("action", choices=["explain", "seed"])
    an.add_argument("--job-id", type=int)
    an.add_argument("--harvested", action="store_true", help="explain against harvested questions, not the live form")

    va = sub.add_parser("vault", help="Candidate Vault: seed the question catalog, import a CV, coverage status")
    va.add_argument("action", choices=["status", "seed", "import-cv"])
    va.add_argument("path", nargs="?", help="import-cv: the resume file (PDF or text)")

    qs = sub.add_parser("questions", help="Harvest real application-form questions (never submits) / write docs/QUESTIONS.md")
    qs.add_argument("action", choices=["harvest", "report", "dump-form"])
    qs.add_argument("--job-id", type=int, help="dump-form: the job whose form to open")
    qs.add_argument("--platform", action="append",
                    help="harvest only these: greenhouse ashby lever smartrecruiters internshala unstop naukri instahyre")
    qs.add_argument("--cap", type=int, default=150, help="max distinct companies per ATS platform")

    uni = sub.add_parser("universe", help="Company universe: seed it or report on it")
    uni.add_argument("action", choices=["seed", "stats", "merges"])
    uni.add_argument("--source", action="append",
                     help="seed: run only this seeder (repeatable): own_data manual portfolios yc inc42 startup_india github")
    uni.add_argument("--limit", type=int, default=100, help="merges: rows to show")

    bl = sub.add_parser("browser-login", help="Open a platform's browser profile to sign in / pass a challenge by hand")
    bl.add_argument("platform", help="naukri | wellfound | yc_waas | internshala | unstop | instahyre")
    bl.add_argument("--record", action="store_true",
                    help="drive the window with Playwright and log the site's API calls (Google sign-in won't work)")
    
    # apply
    apply_p = sub.add_parser("apply", help="Run the application engine")
    apply_p.add_parser = apply_p
    apply_p.add_argument("--limit", type=int, default=10, help="Number of applications to attempt")

    apl = sub.add_parser("appliers", help="Appliers: list them, resolve apply links, coverage of the in-policy pool")
    apl.add_argument("action", choices=["coverage", "list", "resolve"])
    apl.add_argument("--jobs", action="store_true", help="coverage: one line per in-policy job")
    apl.add_argument("--limit", type=int, default=500, help="resolve: Himalayas jobs to resolve")

    co = sub.add_parser("companies", help="Company briefs: fetch once per company, cached permanently")
    co.add_argument("action", choices=["brief"])
    co.add_argument("--company", help="just this company (name, SQL LIKE); prints the brief")
    co.add_argument("--limit", type=int, default=50, help="companies to fetch this run")
    co.add_argument("--refresh", action="store_true", help="fetch again even if a brief is cached")

    ats = sub.add_parser("ats", help="ATS resolution: identify each company's ATS once, cache it")
    ats.add_argument("action", choices=["resolve", "resolve-browser", "stats"])
    ats.add_argument("--headed", action="store_true", help="resolve-browser: show the browser window")
    ats.add_argument("--limit", type=int, default=200, help="resolve: companies to attempt this run")
    ats.add_argument("--company", action="append", help="resolve: just these companies (name, SQL LIKE)")
    ats.add_argument("--no-probe", action="store_true", help="resolve: skip guessing ATS API slugs")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "init": cmd_init,
        "status": cmd_status,
        "dashboard": cmd_dashboard,
        "discover": cmd_discover,
        "apply": cmd_apply,
        "ats": cmd_ats,
        "appliers": cmd_appliers,
        "companies": cmd_companies,
        "browser-login": cmd_browser_login,
        "universe": cmd_universe,
        "questions": cmd_questions,
        "vault": cmd_vault,
        "answers": cmd_answers,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
