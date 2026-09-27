import logging
from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.models.job import Job, SearchRun
from src.api.schemas import JobCreate
from src.acquisition.adapters.yc_startup import YCStartupJobsAdapter
from src.acquisition.adapters.linkedin import LinkedInAdapter
from src.acquisition.adapters.remotive_live import RemotiveLiveAdapter
from src.acquisition.filter import AdvancedJobClassifier

logger = logging.getLogger(__name__)

class AcquisitionManager:
    def __init__(self, mode: str = "mock"):
        self.mode = mode

        # Instantiate adapters with the requested mode
        if mode == "mock":
            self.adapters = [
                YCStartupJobsAdapter(mode="mock"),
                LinkedInAdapter(mode="mock")
            ]
            self.queries = [
                "Remote Data Science Internship", "Mumbai onsite ML internship",
                "Remote ML Engineer", "Full-Time Software Engineer"
            ]
        else:
            self.adapters = [
                YCStartupJobsAdapter(mode="live"),
                LinkedInAdapter(mode="live"),
                RemotiveLiveAdapter(mode="live")
            ]
            self.queries = [
                "Data Science Intern", "Machine Learning Intern", "AI Intern",
                "GenAI Intern", "NLP Intern", "Computer Vision Intern",
                "AI Research Intern", "ML Research Intern", "Data Analytics Intern",
                "Data Engineering Intern", "ML Engineering Intern",
                "Software Engineering Intern", "Backend Engineering Intern",
                "Full Stack Engineering Intern", "Research Assistant AI",
                "Student Researcher ML", "Remote Data Science Internship",
                "Remote ML Internship", "Remote AI Internship",
                "Remote Software Engineering Internship", "Remote Junior Software Engineer",
                "Remote New Grad Software Engineer", "Remote Entry Level Data Scientist",
                "Remote Graduate ML Engineer"
            ]

    async def _archive_old_jobs(self, db: AsyncSession):
        result = await db.execute(select(Job).where(Job.archived == False))
        jobs = result.scalars().all()
        for j in jobs:
            j.archived = True
        await db.commit()

    async def run_acquisition(self, db: AsyncSession) -> Dict[str, Any]:
        await self._archive_old_jobs(db)

        run_record = SearchRun(
            sources_queried=len(self.adapters),
            queries_executed=len(self.queries) * len(self.adapters)
        )
        db.add(run_record)
        await db.flush()

        classifier = AdvancedJobClassifier()

        adapter_stats = []

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
                            continue

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

            # Capture adapter status after its queries are done
            adapter_stats.append(adapter.get_status())

        await db.refresh(run_record)
        return {"run_record": run_record, "adapter_stats": adapter_stats}
