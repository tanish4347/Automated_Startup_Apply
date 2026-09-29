from autoapply.appliers.base import BaseApplier, ApplyResult
from autoapply.models.job import Job
from autoapply.models.application import Application
from autoapply.models.candidate import CandidateProfile
from autoapply.appliers.playwright_utils import get_browser_context, safe_fill, check_for_captcha
from autoapply.logging import get_logger

log = get_logger(__name__)

class BreezyApplier(BaseApplier):
    @property
    def name(self) -> str:
        return 'breezy'

    def can_handle(self, job: Job) -> bool:
        if job.ats_platform == 'breezy':
            return True
        if job.application_url and 'breezy.hr' in job.application_url:
            return True
        return False

    def apply(self, job: Job, application: Application, profile: CandidateProfile) -> ApplyResult:
        url = job.application_url
        if not url:
            return ApplyResult(success=False, error_message='No application URL provided.')

        log.info('breezy_applying', job_id=job.id, url=url)

        with get_browser_context() as context:
            page = context.new_page()
            try:
                page.goto(url, wait_until='networkidle', timeout=30000)
                
                # Try clicking apply button if present
                apply_btn = page.locator('a.apply-button, button.apply-button')
                if apply_btn.count() > 0:
                    apply_btn.first.click()
                    page.wait_for_selector('form', timeout=10000)
                
                if page.locator('form').count() == 0:
                    return ApplyResult(success=False, error_message='Application form not found')
                
                if check_for_captcha(page):
                    return ApplyResult(success=False, error_message='CAPTCHA detected')
                
                safe_fill(page, 'input[name="name"]', profile.full_name)
                safe_fill(page, 'input[name="emailAddress"]', profile.email)
                safe_fill(page, 'input[name="phoneNumber"]', profile.phone)
                
                uploaded = False
                if profile.resume_path:
                    try:
                        input_file = page.locator('input[type="file"]')
                        if input_file.count() > 0:
                            input_file.first.set_input_files(profile.resume_path)
                            uploaded = True
                    except Exception as e:
                        log.warning('breezy_resume_upload_failed', error=str(e))
                
                submit_btn = page.locator('button[type="submit"]')
                if submit_btn.is_visible():
                    submit_btn.click()
                    try:
                        page.wait_for_load_state('networkidle', timeout=10000)
                        if page.locator('text="Thank you"').count() > 0 or page.locator('text="Application submitted"').count() > 0:
                            return ApplyResult(
                                success=True, 
                                confirmation_text='Breezy submission successful',
                                resume_used='pdf_uploaded' if uploaded else 'none'
                            )
                    except Exception as e:
                        return ApplyResult(success=False, error_message=f'Timeout waiting for submission result: {e}')
                
                return ApplyResult(success=False, error_message='Could not submit')
                
            except Exception as e:
                log.exception('breezy_error', error=str(e))
                return ApplyResult(success=False, error_message=f'Browser exception: {e}')
