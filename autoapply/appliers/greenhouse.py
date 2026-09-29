from autoapply.appliers.base import BaseApplier, ApplyResult
from autoapply.models.job import Job
from autoapply.models.application import Application
from autoapply.models.candidate import CandidateProfile
from autoapply.appliers.playwright_utils import get_browser_context, safe_fill, check_for_captcha
from autoapply.appliers.question_engine import answer_custom_question
from autoapply.logging import get_logger

log = get_logger(__name__)

class GreenhouseApplier(BaseApplier):
    @property
    def name(self) -> str:
        return 'greenhouse'

    def can_handle(self, job: Job) -> bool:
        if job.ats_platform == 'greenhouse':
            return True
        if job.application_url and 'greenhouse.io' in job.application_url:
            return True
        return False

    def apply(self, job: Job, application: Application, profile: CandidateProfile) -> ApplyResult:
        url = job.application_url
        if not url:
            return ApplyResult(success=False, error_message='No application URL provided.')
            
        if '/jobs/' in url and '#app' not in url:
            url += '#app'

        log.info('greenhouse_applying', job_id=job.id, url=url)
        answers_used = {}

        with get_browser_context() as context:
            page = context.new_page()
            try:
                page.goto(url, wait_until='networkidle', timeout=30000)
                
                if page.locator('#app').count() == 0:
                    return ApplyResult(success=False, error_message='Application form not found')
                
                if check_for_captcha(page):
                    return ApplyResult(success=False, error_message='CAPTCHA detected on load')
                
                names = (profile.full_name or '').split(' ', 1)
                safe_fill(page, '#first_name', names[0] if names else '')
                safe_fill(page, '#last_name', names[1] if len(names) > 1 else 'Applicant')
                safe_fill(page, '#email', profile.email)
                safe_fill(page, '#phone', profile.phone)
                
                # Handled fields
                handled_texts = ['first name', 'last name', 'email', 'phone', 'resume', 'cv', 'cover letter']
                
                for field_div in page.locator('.custom-question, .field').all():
                    label_el = field_div.locator('label').first
                    if label_el.count() == 0:
                        continue
                        
                    label_text = label_el.text_content().strip()
                    lower_label = label_text.lower()
                    
                    if any(h in lower_label for h in handled_texts):
                        continue
                        
                    input_el = field_div.locator('input[type=\"text\"], textarea, select').first
                    if input_el.count() == 0:
                        continue
                        
                    is_required = '*' in label_text or field_div.locator('.asterisk').count() > 0
                    
                    # Exact matches for known URLs
                    if 'linkedin' in lower_label:
                        input_el.fill(profile.linkedin_url or '')
                        continue
                    elif 'github' in lower_label or 'portfolio' in lower_label or 'website' in lower_label:
                        url_to_use = profile.github_url or profile.portfolio_url or profile.linkedin_url
                        input_el.fill(url_to_use or '')
                        continue
                        
                    # Dynamic Question Engine
                    if is_required and hasattr(profile, 'raw_json'):
                        tag_name = input_el.evaluate('el => el.tagName.toLowerCase()')
                        options = []
                        if tag_name == 'select':
                            options = input_el.locator('option').all_text_contents()
                            options = [o.strip() for o in options if o.strip()]
                            
                        log.info('asking_question_engine', question=label_text)
                        answer = answer_custom_question(
                            question=label_text,
                            question_type=tag_name,
                            options=options,
                            profile_data=profile.raw_json,
                            job_description=job.description_text or job.description_raw or ''
                        )
                        
                        if answer:
                            if tag_name == 'select':
                                try:
                                    input_el.select_option(label=answer)
                                    answers_used[label_text] = answer
                                except Exception:
                                    return ApplyResult(success=False, error_message=f'Failed to select option for {label_text}')
                            else:
                                input_el.fill(answer)
                                answers_used[label_text] = answer
                        else:
                            return ApplyResult(success=False, error_message=f'Cannot answer required question: {label_text}')

                # Upload resume file if we have it
                uploaded = False
                if profile.resume_path:
                    try:
                        input_file = page.locator('input[type=\"file\"]')
                        if input_file.count() > 0:
                            input_file.first.set_input_files(profile.resume_path)
                            uploaded = True
                    except Exception as e:
                        log.warning('greenhouse_resume_upload_failed', error=str(e))
                
                submit_btn = page.locator('#submit_app')
                if submit_btn.is_visible():
                    submit_btn.click()
                    
                    try:
                        page.wait_for_load_state('networkidle', timeout=10000)
                        
                        if check_for_captcha(page):
                            return ApplyResult(success=False, error_message='CAPTCHA detected upon submission')
                            
                        if page.locator('.asterisk').count() > 0 or page.locator('.error-message').count() > 0:
                            return ApplyResult(success=False, error_message='Required custom questions missed')
                            
                        if 'thank' in page.url.lower() or page.locator('text=\"Thank you\"').count() > 0:
                            return ApplyResult(
                                success=True, 
                                confirmation_text='Greenhouse submission successful',
                                resume_used='pdf_uploaded' if uploaded else 'none',
                                answers_submitted=answers_used
                            )
                            
                    except Exception as e:
                        return ApplyResult(success=False, error_message=f'Timeout waiting for submission result: {e}')
                
                return ApplyResult(success=False, error_message='Could not submit')
                
            except Exception as e:
                log.exception('greenhouse_error', error=str(e))
                return ApplyResult(success=False, error_message=f'Browser exception: {e}')
