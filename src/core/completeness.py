from src.models.candidate import CandidateProfileExt

class ProfileCompletenessCalculator:
    @staticmethod
    def calculate(profile: CandidateProfileExt) -> dict:
        if not profile:
            return {"overall": 0, "application": 0, "bgv": 0, "onboarding": 0, "missing": ["Profile not created"]}

        missing = []

        app_score = 0
        if profile.first_name and profile.email:
            app_score += 50
        else:
            missing.append("Basic contact info (Name, Email)")

        if profile.education: app_score += 25
        else: missing.append("Education history")

        if profile.employment: app_score += 25
        else: missing.append("Employment history")

        bgv_score = 0
        if profile.addresses: bgv_score += 50
        else: missing.append("Address history for BGV")

        if profile.compliance: bgv_score += 50
        else: missing.append("Compliance questionnaire")

        onb_score = 0
        if profile.sensitive_info and profile.sensitive_info.pan: onb_score += 50
        else: missing.append("PAN Card for onboarding")

        if profile.documents: onb_score += 50
        else: missing.append("Required onboarding documents")

        overall = (app_score + bgv_score + onb_score) // 3

        return {
            "overall": overall,
            "application": app_score,
            "bgv": bgv_score,
            "onboarding": onb_score,
            "missing": missing
        }
