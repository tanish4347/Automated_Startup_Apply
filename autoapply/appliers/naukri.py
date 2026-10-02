"""Naukri applier (apply_channel=naukri). Clicking Apply IS the submission: see docs/NAUKRI_FORM.md.

There is no form between the posting and the application, so the harness runs this applier with
click_is_submit: review_only opens the posting read-only, screenshots it and parks BEFORE the click;
live mode clicks, then only the success assertion makes it submitted.

open_form (no request at all for a job whose stored application_url is already off-Naukri):
  - application_url on another host: Reroute to it (13 of the 18 in-policy jobs: "apply on company
    site", the URL captured at discovery from the job's own applyRedirectUrl).
  - GET the posting; the page's own /jobapi/v4/job/<id> response is captured. applyRedirectUrl off
    naukri.com or companyApplyJob: Reroute (never click "Apply on company site": its handler opens
    the URL and also tells Naukri you applied there, /showAcp).
  - walk-in ("I am interested") or hideApplyButton: nothing to apply to (LookupError).
  - #login_Layer visible: not logged in -> NeedsLogin.
  - span#already-applied: already applied on Naukri (LookupError; the job is never re-tried).
  - button#apply-button must be visible; it is the submit control.

Success assertion (explicit): after the click, span#already-applied ("Applied") is shown AND no
questionnaire chatbot drawer is open. A job with a recruiter questionnaire opens Naukri's chatbot
after the apply call; its questions are not answered automatically, so that attempt is uncertain
and goes to /review. PROVISIONAL until a live application is observed.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from autoapply.appliers.harness import FormApplier, NeedsLogin, Reroute
from autoapply.models.job import Job

CHATBOT = "[class*=chatbot]:visible, [class*=ChatBot]:visible, [id*=chatbot]:visible"


def _off_naukri(url: str | None) -> bool:
    host = urlsplit(url or "").netloc.lower()
    return bool(host) and not host.endswith("naukri.com")


class NaukriApplier(FormApplier):
    platform = "naukri"
    channels = ("naukri",)
    click_is_submit = True

    def open_form(self, bs, job: Job) -> None:
        if _off_naukri(job.application_url):
            raise Reroute(job.application_url, f"Naukri posting applies on the company site: {job.application_url}")
        captured = bs.goto(job.source_url, capture_response="/jobapi/v4/job/") or {}
        d = captured.get("jobDetails") or captured
        redirect = d.get("applyRedirectUrl")
        if _off_naukri(redirect):
            raise Reroute(redirect, f"Naukri posting applies on the company site: {redirect}")
        if d.get("companyApplyJob"):
            raise LookupError("naukri: company-site job without a usable redirect URL")
        if d.get("walkIn"):
            raise LookupError("naukri: walk-in posting; there is no online application")
        if d.get("hideApplyButton"):
            raise LookupError("naukri: the posting hides its Apply button")
        bs.page.wait_for_timeout(2000)
        if bs.page.locator("#login_Layer:visible").count():
            raise NeedsLogin("Naukri is not logged in. Run `autoapply browser-login naukri`, sign in, close the window.")
        if bs.page.locator("#already-applied").count():
            raise LookupError("naukri: already applied to this posting on Naukri")
        if bs.page.locator("#company-site-button").count():
            raise LookupError("naukri: 'Apply on company site' shown but the job JSON had no redirect URL")
        if bs.page.locator("#apply-button:visible").count() == 0:
            raise LookupError("naukri: no visible Apply button")

    def read_fields(self, bs):
        return []

    def submit_control(self, bs):
        return bs.page.locator("#apply-button").first

    def success_assertion(self, bs, before_url: str) -> str | None:
        if bs.page.locator(CHATBOT).count():
            return None     # the questionnaire chatbot is open: not finished
        if bs.page.locator("#already-applied:visible").count():
            return "Naukri shows span#already-applied (\"Applied\") after the click, no chatbot open"
        return None
