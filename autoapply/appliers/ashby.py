from autoapply.appliers.base import BaseApplier, ApplyResult
from autoapply.models.job import Job
from autoapply.models.application import Application
from autoapply.models.vault import VaultIdentity
from autoapply.appliers.playwright_utils import get_browser_context, safe_fill, check_for_captcha
from autoapply.logging import get_logger

log = get_logger(__name__)

class AshbyApplier(BaseApplier):
    @property
    def name(self) -> str:
        return 'ashby'

    def can_handle(self, job: Job) -> bool:
        if job.ats_platform == 'ashby':
            return True
        if job.application_url and 'ashbyhq.com' in job.application_url:
            return True
        return False

    def apply(self, job: Job, application: Application, profile: VaultIdentity) -> ApplyResult:
        url = job.application_url
        if not url:
            return ApplyResult(success=False, error_message='No application URL provided.')
            
        if '/application' not in url:
            url = url.rstrip('/') + '/application'

        log.info('ashby_applying', job_id=job.id, url=url)

        with get_browser_context() as context:
            page = context.new_page()
            try:
                page.goto(url, wait_until='networkidle', timeout=30000)
                
                if check_for_captcha(page):
                    return ApplyResult(success=False, error_message='CAPTCHA detected on load')
                
                if page.locator('text=\"This position is no longer accepting applications\"').count() > 0:
                    return ApplyResult(success=False, error_message='Job closed')
                
                uploaded = False
                if profile.resume_path:
                    try:
                        input_file = page.locator('input[type=\"file\"]')
                        if input_file.count() > 0:
                            input_file.first.set_input_files(profile.resume_path)
                            uploaded = True
                    except Exception as e:
                        log.warning('ashby_resume_upload_failed', error=str(e))
                
                safe_fill(page, 'input[name=\"name\"]', profile.full_name)
                safe_fill(page, 'input[name=\"email\"]', profile.email)
                safe_fill(page, 'input[name=\"phone\"]', profile.phone)
                
                submit_btn = page.locator('button[type=\"submit\"]')
                if submit_btn.is_visible():
                    submit_btn.click()
                    
                    try:
                        page.wait_for_load_state('networkidle', timeout=10000)
                        
                        if check_for_captcha(page):
                            return ApplyResult(success=False, error_message='CAPTCHA detected upon submission')
                            
                        if page.locator('[aria-invalid=\"true\"]').count() > 0 or page.locator('text=\"required\"').count() > 0:
                            return ApplyResult(success=False, error_message='Required fields missing')
                            
                        if 'success' in page.url.lower() or page.locator('text=\"Application Submitted\"').count() > 0:
                            return ApplyResult(
                                success=True, 
                                confirmation_text='Ashby submission successful',
                                resume_used='pdf_uploaded' if uploaded else 'none'
                            )
                    except Exception as e:
                        return ApplyResult(success=False, error_message=f'Timeout after submission: {e}')
                
                return ApplyResult(success=False, error_message='Submit button not found')
                
            except Exception as e:
                log.exception('ashby_error', error=str(e))
                return ApplyResult(success=False, error_message=f'Browser exception: {e}')
