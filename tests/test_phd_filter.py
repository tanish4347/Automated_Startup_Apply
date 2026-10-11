import pytest
from autoapply.sources.filter import evaluate_job
from autoapply.config import SearchConfig

def test_phd_filter():
    j1 = {"title": "Research Intern", "description_text": "Must be enrolled in a PhD program.", "job_type": "internship"}
    j2 = {"title": "Software Engineering Intern", "description_text": "Pursuing a Bachelor's, Master's, or PhD in Computer Science.", "job_type": "internship"}
    j3 = {"title": "Software Intern", "description_text": "Looking for students.", "job_type": "internship"}
    
    cfg = SearchConfig(queries=[], target_keywords=["software", "research"])
    
    # Needs to match PhD exactly and correctly
    res1 = evaluate_job(j1, cfg)
    assert res1["classification_status"] == "AUTO_REJECT"
    assert "PhD" in res1["reject_reason"]
    
    res2 = evaluate_job(j2, cfg)
    assert res2["classification_status"] != "AUTO_REJECT"
    
    res3 = evaluate_job(j3, cfg)
    assert res3["classification_status"] != "AUTO_REJECT"
