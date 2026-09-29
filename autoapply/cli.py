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
    """Open the platform's persistent browser profile, headed, for a one-time manual login or
    challenge. Everything is done by hand; the profile keeps the cookies for later runs."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    from autoapply.sources.browser import BrowserSession, assert_platform_allowed
    assert_platform_allowed(args.platform)
    if args.platform not in LOGIN_URLS:
        sys.exit(f"Unknown platform {args.platform!r}. Choose from: {', '.join(LOGIN_URLS)}")
    with BrowserSession(args.platform, headless=False) as s:
        s.page.goto(LOGIN_URLS[args.platform], wait_until="domcontentloaded")
        print(f"A browser window is open on {LOGIN_URLS[args.platform]}.\n"
              "Sign in and/or solve any challenge by hand, browse to the job search once, "
              "then close the window to save the profile.")
        s.page.wait_for_event("close", timeout=0)
    print(f"Saved. Profile: {s.profile_dir}\nCalls the site made: {s.calls_path}")


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
        st = ats_stats(session)
        print(f"\nCompanies: {st['companies']}  resolved: {st['resolved']} (India: {st['resolved_india']})  "
              f"never tried: {st['never_tried']}  backing off: {st['failing']}  generic crawl: {st['generic_crawl']}")
        print(f"\n{'platform':<16}{'companies':>10}{'india':>8}")
        for ats, (n, india) in st["by_platform"].items():
            print(f"{ats:<16}{n:>10}{india:>8}")


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

    qs = sub.add_parser("questions", help="Harvest real application-form questions (never submits) / write docs/QUESTIONS.md")
    qs.add_argument("action", choices=["harvest", "report"])
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
    
    # apply
    apply_p = sub.add_parser("apply", help="Run the application engine")
    apply_p.add_parser = apply_p
    apply_p.add_argument("--limit", type=int, default=10, help="Number of applications to attempt")

    ats = sub.add_parser("ats", help="ATS resolution: identify each company's ATS once, cache it")
    ats.add_argument("action", choices=["resolve", "stats"])
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
        "browser-login": cmd_browser_login,
        "universe": cmd_universe,
        "questions": cmd_questions,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
