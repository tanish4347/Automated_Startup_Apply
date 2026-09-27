from src.models.candidate import CandidateProfileExt, Education, Employment

class CVImportService:
    def __init__(self):
        pass

    async def import_cv(self, text_content: str, db_session) -> CandidateProfileExt:
        profile = CandidateProfileExt(
            first_name="Imported",
            last_name="Candidate",
            email="imported@example.com",
            phone="+1234567890",
            summary="This summary was extracted from CV."
        )
        db_session.add(profile)
        await db_session.flush()

        edu = Education(
            profile_id=profile.id,
            institution="University of Mock",
            degree="B.S. Computer Science",
            field="Engineering",
            start_date="2018",
            end_date="2022"
        )
        db_session.add(edu)

        emp = Employment(
            profile_id=profile.id,
            company="Mock Tech Inc",
            title="Software Engineer",
            start_date="2022-06-01",
            current=True,
            description="Extracted from CV."
        )
        db_session.add(emp)

        await db_session.commit()
        return profile
