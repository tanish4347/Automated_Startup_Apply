"""Greenhouse applier (harness; see autoapply/appliers/ats.py).

Form URL (checked read-only 2026-10-02 on Stripe, job 2658): the embed form
  https://job-boards.greenhouse.io/embed/job_app?for=<board token>&token=<job id>
renders the application on Greenhouse's own host for every board. The hosted job page
(job-boards.greenhouse.io/<token>/jobs/<id>) redirects to the company's site when the company
hosts its own careers page (Stripe does), so it is not used. Job ids come from /jobs/<id> in a
Greenhouse URL or from gh_jid=<id> on a company careers URL; the token from the URL path, `for=`,
or the company's resolved ats_token.
The Stripe form: 31 fields, form#application-form, react-select comboboxes (country, location,
school, degree, yes/no questions), reCAPTCHA Enterprise loaded invisibly (never solved: a
challenge on submit leaves the attempt uncertain), submit button[type=submit] "Submit application".

Success assertion (explicit): the URL moves to .../confirmation, or the form is gone and the page
says "Thank you for applying" / "Application submitted". The OLD applier treated `.asterisk`
after submit as an error; `.asterisk` is the required-field marker present on every form, so it
is not used for anything. PROVISIONAL until a live submission is observed.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from autoapply.appliers.ats import AtsApplier, query_param
from autoapply.models.job import Job

SUCCESS_TEXT = re.compile(r"thank you for applying|application (has been )?(submitted|received)", re.I)


class GreenhouseApplier(AtsApplier):
    platform = "greenhouse"
    hosts = ("greenhouse.io",)

    def url_for(self, job: Job) -> str | None:
        hit = super().url_for(job)
        if hit:
            return hit
        return next((u for u in (job.resolved_apply_url, job.application_url, job.source_url)
                     if u and query_param(u, "gh_jid")), None)

    def apply_url(self, job: Job) -> str:
        url = self.url_for(job) or ""
        m = re.search(r"/([\w-]+)/jobs/(\d+)", urlsplit(url).path) if "greenhouse.io" in url else None
        token = (m.group(1) if m else None) or query_param(url, "for") or self._company_token(job)
        job_id = (m.group(2) if m else None) or query_param(url, "token") or query_param(url, "gh_jid")
        if not (token and job_id):
            raise LookupError(f"greenhouse: no board token / job id in {url!r}")
        return f"https://job-boards.greenhouse.io/embed/job_app?for={token}&token={job_id}"

    @staticmethod
    def _company_token(job: Job) -> str | None:
        c = job.company_ref
        if c is not None and c.ats_type == "greenhouse" and c.ats_token:
            return c.ats_token
        return (job.company or "").lower().replace(" ", "") or None

    def submit_control(self, bs):
        return bs.page.locator("#application-form button[type=submit], #submit_app").first

    def success_assertion(self, bs, before_url: str) -> str | None:
        url = bs.page.url
        if "/confirmation" in urlsplit(url).path:
            return f"URL moved to the confirmation page {url}"
        form_gone = bs.page.locator("#application-form, #application_form").count() == 0
        m = SUCCESS_TEXT.search(bs.page.inner_text("body")[:20000])
        if m and form_gone:
            return f"form gone and confirmation text {m.group(0)!r} at {url}"
        return None
