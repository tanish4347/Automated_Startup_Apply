from typing import Iterator
import os
import json
import requests

from autoapply.sources.base import BaseSource, SourceResult
from autoapply.logging import get_logger

log = get_logger(__name__)

class CareerPagesSource(BaseSource):
    @property
    def name(self) -> str:
        return 'career_pages'

    def discover(self) -> Iterator[SourceResult]:
        config_path = os.path.join(os.getcwd(), 'career_pages.json')
        companies = []
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                companies = json.load(f)

        headers = {'User-Agent': 'Mozilla/5.0'}
        
        for c in companies:
            name = c.get('company')
            board_token = c.get('board_token', name.lower().replace(' ', '').replace('-', ''))
            scanned = False
            
            # Greenhouse
            if not scanned:
                try:
                    resp = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true", headers=headers, timeout=5)
                    if resp.status_code == 200:
                        jobs = resp.json().get('jobs', [])
                        log.info('greenhouse_board_found', company=name, count=len(jobs))
                        for j in jobs:
                            yield SourceResult(
                                title=j.get('title', ''), company=name, source=self.name,
                                location=j.get('location', {}).get('name', ''),
                                application_url=j.get('absolute_url', ''), description_text=j.get('content', ''),
                                description_raw=j.get('content', ''), ats_platform='greenhouse', source_id=str(j.get('id', ''))
                            )
                        scanned = True
                except: pass

            # Lever
            if not scanned:
                try:
                    resp = requests.get(f"https://api.lever.co/v0/postings/{board_token}", headers=headers, timeout=5)
                    if resp.status_code == 200:
                        jobs = resp.json()
                        log.info('lever_board_found', company=name, count=len(jobs))
                        for j in jobs:
                            yield SourceResult(
                                title=j.get('text', ''), company=name, source=self.name,
                                location=j.get('categories', {}).get('location', ''),
                                application_url=j.get('hostedUrl', ''), description_text=j.get('descriptionPlain', ''),
                                description_raw=j.get('description', ''), ats_platform='lever', source_id=str(j.get('id', ''))
                            )
                        scanned = True
                except: pass

            # Ashby
            if not scanned:
                try:
                    resp = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{board_token}", headers=headers, timeout=5)
                    if resp.status_code == 200:
                        jobs = resp.json().get('jobs', [])
                        log.info('ashby_board_found', company=name, count=len(jobs))
                        for j in jobs:
                            yield SourceResult(
                                title=j.get('title', ''), company=name, source=self.name,
                                location=j.get('location', ''), application_url=j.get('jobUrl', ''),
                                description_text=j.get('descriptionHtml', ''), description_raw=j.get('descriptionHtml', ''),
                                ats_platform='ashby', source_id=str(j.get('id', ''))
                            )
                        scanned = True
                except: pass

            # SmartRecruiters
            if not scanned:
                try:
                    resp = requests.get(f"https://api.smartrecruiters.com/v1/companies/{board_token}/postings", headers=headers, timeout=5)
                    if resp.status_code == 200:
                        jobs = resp.json().get('content', [])
                        log.info('smartrecruiters_board_found', company=name, count=len(jobs))
                        for j in jobs:
                            yield SourceResult(
                                title=j.get('name', ''), company=name, source=self.name,
                                location=j.get('location', {}).get('city', ''), application_url=f"https://jobs.smartrecruiters.com/{board_token}/{j.get('id')}",
                                description_text='', description_raw='',
                                ats_platform='smartrecruiters', source_id=str(j.get('id', ''))
                            )
                        scanned = True
                except: pass
                
            # Workable
            if not scanned:
                try:
                    resp = requests.post(f"https://apply.workable.com/api/v3/accounts/{board_token}/jobs", headers=headers, json={"query": "", "location": [], "department": [], "worktype": [], "remote": []}, timeout=5)
                    if resp.status_code == 200:
                        jobs = resp.json().get('results', [])
                        log.info('workable_board_found', company=name, count=len(jobs))
                        for j in jobs:
                            yield SourceResult(
                                title=j.get('title', ''), company=name, source=self.name,
                                location=j.get('location', {}).get('city', ''), application_url=f"https://apply.workable.com/{board_token}/j/{j.get('shortcode')}/",
                                description_text='', description_raw='',
                                ats_platform='workable', source_id=j.get('shortcode', '')
                            )
                        scanned = True
                except: pass

