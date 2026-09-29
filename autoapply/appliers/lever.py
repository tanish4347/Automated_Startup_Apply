from autoapply.appliers.base import BaseApplier, ApplyResult
from autoapply.models.job import Job
from autoapply.models.application import Application
from autoapply.models.vault import VaultIdentity
from autoapply.appliers.playwright_utils import get_browser_context, safe_fill, check_for_captcha
from autoapply.logging import get_logger

log = get_logger(__name__)

class LeverApplier(BaseApplier):
    @property
    def name(self) -> str:
        return 'lever'

    def can_handle(self, job: Job) -> bool:
        if job.ats_platform == 'lever':
            return True
        if job.application_url and 'lever.co' in job.application_url:
            return True
        return False

    def apply(self, job: Job, application: Application, profile: VaultIdentity) -> ApplyResult:
        url = job.application_url
        if not url:
            return ApplyResult(success=False, error_message='No application URL provided.')
            
        if '/apply' not in url:
            url = url.rstrip('/') + '/apply'

        log.info('lever_applying', job_id=job.id, url=url)

        with get_browser_context() as context:
            page = context.new_page()
            try:
                page.goto(url, wait_until='networkidle', timeout=30000)
                
                if page.locator('form').count() == 0:
                    return ApplyResult(success=False, error_message='Application form not found')
                
                if check_for_captcha(page):
                    return ApplyResult(success=False, error_message='CAPTCHA detected on load')
                
                # Upload resume
                uploaded = False
                if profile.resume_path:
                    try:
                        input_file = page.locator('input[type=\"file\"][name=\"resume\"]')
                        if input_file.count() > 0:
                            input_file.first.set_input_files(profile.resume_path)
                            uploaded = True
                    except Exception as e:
                        log.warning('lever_resume_upload_failed', error=str(e))
                
                safe_fill(page, 'input[name=\"name\"]', profile.full_name)
                safe_fill(page, 'input[name=\"email\"]', profile.email)
                safe_fill(page, 'input[name=\"phone\"]', profile.phone)
                safe_fill(page, 'input[name=\"org\"]', profile.location)
                
                safe_fill(page, 'input[name=\"urls[LinkedIn]\"]', profile.linkedin_url)
                safe_fill(page, 'input[name=\"urls[GitHub]\"]', profile.github_url)
                safe_fill(page, 'input[name=\"urls[Portfolio]\"]', profile.portfolio_url)
                
                submit_btn = page.locator('button:has-text(\"Submit application\")')
                if submit_btn.is_visible():
                    submit_btn.click()
                    
                    try:
                        page.wait_for_load_state('networkidle', timeout=10000)
                        
                        if check_for_captcha(page):
                            return ApplyResult(success=False, error_message='CAPTCHA detected upon submission')
                            
                        if page.locator('.error-message').count() > 0 or page.locator('[data-qa=\"error-message\"]').count() > 0:
                            return ApplyResult(success=False, error_message='Required fields or resume missing')
                            
                        if 'thanks' in page.url.lower():
                            return ApplyResult(
                                success=True, 
                                confirmation_text='Lever submission successful',
                                resume_used='pdf_uploaded' if uploaded else 'none'
                            )
                    except Exception as e:
                        return ApplyResult(success=False, error_message=f'Timeout after submission: {e}')
                
                return ApplyResult(success=False, error_message='Submit button not found')
                
            except Exception as e:
                log.exception('lever_error', error=str(e))
                return ApplyResult(success=False, error_message=f'Browser exception: {e}')
