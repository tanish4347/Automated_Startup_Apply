import argparse
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.session import engine, AsyncSessionLocal, Base
from src.acquisition.manager import AcquisitionManager
from src.models.job import Job
from sqlalchemy.future import select

async def run_discovery(mode: str):
    print(f"\n==================================================")
    print(f"STARTING {mode.upper()} ACQUISITION PIPELINE")
    print(f"==================================================\n")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    manager = AcquisitionManager(mode=mode)

    async with AsyncSessionLocal() as db:
        result = await manager.run_acquisition(db)
        run_record = result["run_record"]
        adapter_stats = result["adapter_stats"]

        print("==================================================")
        print("SOURCE REPORT")
        print("==================================================")
        for stat in adapter_stats:
            print(f"Source: {stat['name']}")
            print(f"Attempted: {stat['attempted']}")
            print(f"Succeeded: {stat['succeeded']}")
            print(f"Failed: {stat['failed']}")
            print(f"Limitations: {stat['limitations']}")
            print("-" * 30)

        print("\n==================================================")
        print("DISCOVERY REPORT")
        print("==================================================")
        print(f"Total raw jobs retrieved: {run_record.total_discovered}")
        print(f"Duplicates removed: {run_record.duplicates_removed}")
        print(f"Total unique jobs: {run_record.total_discovered - run_record.duplicates_removed}")
        print(f"Total accepted: {run_record.accepted}")
        print(f"Total rejected: {run_record.rejected}")

        print("\nAccepted breakdown:")
        print(f"Remote internships: {run_record.remote_internships}")
        print(f"Non-remote internships: {run_record.non_remote_internships}")
        print(f"Remote entry-level full-time: {run_record.remote_entry_level_ft}")

        print("\nRejected breakdown:")
        print(f"Wrong field: {run_record.rej_wrong_field}")
        print(f"Non-internship/non-remote full-time: {run_record.rej_non_remote_ft}")
        print(f"Senior/experienced: {run_record.rej_senior}")
        print(f"Mumbai non-remote: {run_record.rej_mumbai_non_remote}")
        print(f"Other: {run_record.rej_other}")

        print(f"\nNumber of real sources queried: {run_record.sources_queried}")
        print(f"Number of search queries executed: {run_record.queries_executed}")

        print("\n==================================================")
        print("SAMPLE ACCEPTED JOBS")
        print("==================================================")
        res = await db.execute(select(Job).where(Job.search_run_id == run_record.id, Job.is_target_role == True).limit(20))
        accepted_jobs = res.scalars().all()
        for i, j in enumerate(accepted_jobs):
            print(f"{i+1}. Title: {j.role} | Company: {j.company} | URL: {j.original_url}")
            print(f"   Field: {j.target_field} | Category: {j.role_type} | Mode: {j.work_mode}")
            print(f"   Loc: {j.location} | Exp Req: {j.experience_required} | Score: {j.relevance_score} | Source: {j.source}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Automated Job System CLI")
    parser.add_argument("--discover", action="store_true", help="Run discovery pipeline")
    parser.add_argument("--mode", choices=["mock", "live"], default="mock", help="Mode to run discovery in")
    args = parser.parse_args()

    if args.discover:
        asyncio.run(run_discovery(args.mode))
