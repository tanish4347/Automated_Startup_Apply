from autoapply.appliers.base import BaseApplier, ApplyResult
from autoapply.models.job import Job
from autoapply.models.application import Application
from autoapply.models.candidate import CandidateProfile
from autoapply.appliers.playwright_utils import get_browser_context, safe_fill, check_for_captcha
from autoapply.logging import get_logger

log = get_logger(__name__)

class SmartRecruitersApplier(BaseApplier):
    @property
    def name(self) -> str:
        return 'smartrecruiters'

    def can_handle(self, job: Job) -> bool:
        if job.ats_platform == 'smartrecruiters':
            return True
        if job.application_url and 'smartrecruiters.com' in job.application_url:
            return True
        return False

    def apply(self, job: Job, application: Application, profile: CandidateProfile) -> ApplyResult:
        url = job.application_url
        if not url:
            return ApplyResult(success=False, error_message='No application URL provided.')

        log.info('smartrecruiters_applying', job_id=job.id, url=url)

        with get_browser_context() as context:
            page = context.new_page()
            try:
                page.goto(url, wait_until='networkidle', timeout=30000)
                
                # Check if we are on the main job page and need to click Apply
                apply_btn = page.locator('#st-apply')
                if apply_btn.count() > 0 and apply_btn.first.is_visible():
                    apply_btn.first.click()
                    page.wait_for_load_state('networkidle', timeout=10000)
                
                if check_for_captcha(page):
                    return ApplyResult(success=False, error_message='CAPTCHA detected')
                
                uploaded = False
                if profile.resume_path:
                    try:
                        input_file = page.locator('input[type=\"file\"]')
                        if input_file.count() > 0:
                            input_file.first.set_input_files(profile.resume_path)
                            uploaded = True
                    except Exception as e:
                        log.warning('sr_resume_upload_failed', error=str(e))
                
                names = (profile.full_name or '').split(' ', 1)
                safe_fill(page, 'input[name=\"firstName\"]', names[0] if names else '')
                safe_fill(page, 'input[name=\"lastName\"]', names[1] if len(names) > 1 else 'Applicant')
                safe_fill(page, 'input[name=\"email\"]', profile.email)
                safe_fill(page, 'input[name=\"phoneNumber\"]', profile.phone)
                
                submit_btn = page.locator('button[data-test=\"submit-button\"]')
                if submit_btn.is_visible():
                    submit_btn.click()
                    
                    try:
                        page.wait_for_load_state('networkidle', timeout=10000)
                        
                        if check_for_captcha(page):
                            return ApplyResult(success=False, error_message='CAPTCHA detected upon submission')
                            
                        if page.locator('[aria-invalid=\"true\"]').count() > 0 or page.locator('.is-invalid').count() > 0:
                            return ApplyResult(success=False, error_message='Required fields missing')
                            
                        if 'success' in page.url.lower() or 'thank' in page.url.lower():
                            return ApplyResult(
                                success=True, 
                                confirmation_text='SmartRecruiters submission successful',
                                resume_used='pdf_uploaded' if uploaded else 'none'
                            )
                    except Exception as e:
                        return ApplyResult(success=False, error_message=f'Timeout after submission: {e}')
                
                return ApplyResult(success=False, error_message='Submit button not found')
                
            except Exception as e:
                log.exception('smartrecruiters_error', error=str(e))
                return ApplyResult(success=False, error_message=f'Browser exception: {e}')
