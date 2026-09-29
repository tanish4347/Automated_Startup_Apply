import time
import os
import sys

from autoapply.config import get_settings
from autoapply.models.base import engine_from_settings, get_session_factory
from autoapply.sources.career_pages import CareerPagesSource

def test_career_pages():
    settings = get_settings()
    engine = engine_from_settings(settings.db.url, settings.db.echo)
    SessionLocal = get_session_factory(engine)

    source = CareerPagesSource()
    print("Testing CareerPagesSource...")
    
    total = 0
    with SessionLocal() as session:
        for result in source.discover():
            total += 1
            if total % 100 == 0:
                print(f"Found {total} jobs...")
                
    print(f"CareerPagesSource finished. Found {total} total jobs directly from ATS APIs.")

if __name__ == '__main__':
    test_career_pages()
