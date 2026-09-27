import logging
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.models.job import Job, SearchRun
from src.api.schemas import JobCreate
from src.acquisition.adapters.yc_startup import YCStartupJobsAdapter
from src.acquisition.adapters.linkedin import LinkedInAdapter
from src.acquisition.filter import AdvancedJobClassifier

logger = logging.getLogger(__name__)

class AcquisitionManager:
    def __init__(self):
        self.adapters = [YCStartupJobsAdapter(), LinkedInAdapter()]

        self.queries = [
            "Data Science Internship", "Machine Learning Internship", "AI Internship",
            "Remote Data Science Internship", "Mumbai onsite ML internship",
            "Remote ML Engineer", "Full-Time Software Engineer"
        ]

    async def _archive_old_jobs(self, db: AsyncSession):
        result = await db.execute(select(Job).where(Job.archived == False))
        jobs = result.scalars().all()
        for j in jobs:
            j.archived = True
        await db.commit()

    async def run_acquisition(self, db: AsyncSession) -> SearchRun:
        await self._archive_old_jobs(db)

        run_record = SearchRun(
            sources_queried=len(self.adapters),
            queries_executed=len(self.queries) * len(self.adapters)
        )
        db.add(run_record)
        await db.flush() # get ID

        classifier = AdvancedJobClassifier()

        for adapter in self.adapters:
            for query in self.queries:
                try:
                    jobs: List[JobCreate] = await adapter.fetch_jobs(query)
                    run_record.total_discovered += len(jobs)

                    for job_data in jobs:
                        # Deduplication check
                        existing = await db.execute(select(Job).where(Job.original_url == job_data.original_url))
                        if existing.scalar_one_or_none():
                            run_record.duplicates_removed += 1
                            continue

                        # Evaluate role
                        eval_res = classifier.evaluate(job_data.role, job_data.description, job_data.location, job_data.work_mode)

                        if eval_res["is_target_role"]:
                            run_record.accepted += 1
                            if eval_res["role_type"] == "REMOTE_INTERNSHIP": run_record.remote_internships += 1
                            elif eval_res["role_type"] == "NON_REMOTE_INTERNSHIP": run_record.non_remote_internships += 1
                            elif eval_res["role_type"] == "REMOTE_ENTRY_LEVEL_FULL_TIME": run_record.remote_entry_level_ft += 1
                        else:
                            run_record.rejected += 1
                            cat = eval_res.get("rejection_category", "Other")
                            if cat == "Wrong field": run_record.rej_wrong_field += 1
                            elif cat == "Non-internship/non-remote full-time": run_record.rej_non_remote_ft += 1
                            elif cat == "Senior/experienced": run_record.rej_senior += 1
                            elif cat == "Mumbai non-remote": run_record.rej_mumbai_non_remote += 1
                            else: run_record.rej_other += 1
                            continue # Do not save rejected jobs to DB entirely per requirements of keeping active db clean

                        # Save Accepted Job
                        job_dict = job_data.model_dump()
                        job_dict.update({
                            "is_target_role": eval_res["is_target_role"],
                            "role_type": eval_res["role_type"],
                            "target_field": eval_res["target_field"],
                            "work_mode": eval_res["work_mode"],
                            "seniority": eval_res["seniority"],
                            "relevance_score": eval_res["relevance_score"],
                            "filter_reason": eval_res["filter_reason"],
                            "search_run_id": run_record.id,
                            "archived": False
                        })
                        new_job = Job(**job_dict)
                        db.add(new_job)

                    await db.commit()
                except Exception as e:
                    logger.error(f"Error fetching: {e}")
                    await db.rollback()

        await db.refresh(run_record)
        return run_record
