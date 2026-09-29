import json
import os
from pydantic import BaseModel, Field
from autoapply.logging import get_logger

log = get_logger(__name__)

class AnswerResult(BaseModel):
    answer: str = Field(description="The determined answer to the question.")
    confidence: str = Field(description="HIGH if factual/known, GENERATED if inferred, UNKNOWN if data is missing.")

def answer_custom_question(question: str, question_type: str, options: list[str], profile_data: dict, job_description: str) -> str | None:
    """
    Uses Gemini to answer dynamic questions.
    Returns the string answer, or None if unknown/factual information is missing.
    """
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        log.warning("no_gemini_key_for_questions", question=question[:30])
        return None
        
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        
        prompt = f"""
        You are an automated job application assistant answering a single question.
        
        Candidate Profile JSON:
        {json.dumps(profile_data)}
        
        Job Description Excerpt:
        {job_description[:2000]}
        
        Question: {question}
        Input Type: {question_type}
        Options (if any): {options}
        
        RULES:
        1. If it's a known factual question (e.g. Do you require sponsorship?), answer from the profile.
        2. If it's a role-specific essay (e.g. Why this company?), generate a concise, professional answer combining the profile and JD.
        3. If it requires factual information NOT in the profile (e.g. "What is your GPA?" but GPA is missing), output UNKNOWN for confidence. DO NOT INVENT FACTS.
        4. If Options are provided, your answer MUST match one of the options exactly.
        """
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={
                'response_mime_type': 'application/json',
                'response_schema': AnswerResult,
                'temperature': 0.1
            }
        )
        
        result = AnswerResult.model_validate_json(response.text)
        
        if result.confidence == 'UNKNOWN':
            log.warning("question_unknown", question=question[:30])
            return None
            
        return result.answer
        
    except Exception as e:
        log.error("question_generation_failed", error=str(e))
        return None
