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
from autoapply.models import Base, engine_from_settings, get_session_factory


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize the database."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    log = get_logger("cli.init")

    engine = engine_from_settings(settings.db.url, settings.db.echo)
    Base.metadata.create_all(engine)
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
    from autoapply.sources.orchestrator import run_discovery
    print("Starting discovery...")
    run_discovery()
    print("Discovery complete.")


def cmd_apply(args: argparse.Namespace) -> None:
    """Run application engine."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    from autoapply.appliers.orchestrator import run_application_engine
    print(f"Starting application engine (limit: {args.limit})...")
    run_application_engine(limit=args.limit)
    print("Application engine complete.")


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
    sub.add_parser("discover", help="Run job discovery sources")
    
    # apply
    apply_p = sub.add_parser("apply", help="Run the application engine")
    apply_p.add_parser = apply_p
    apply_p.add_argument("--limit", type=int, default=10, help="Number of applications to attempt")

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
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
