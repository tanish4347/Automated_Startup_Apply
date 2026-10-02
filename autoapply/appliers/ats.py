"""Shared base for the classic-ATS appliers (Greenhouse, Lever, Ashby, SmartRecruiters).

Each is a harness FormApplier (autoapply/appliers/harness.py): review_only by default, answers from
the answer engine, evidence per attempt, and an explicit per-platform success assertion. The old
bespoke appliers (pre-harness BaseApplier: their own browser, Gemini answers, name splitting,
"no exception = success") are gone.

A job belongs to an ATS applier when its ats_platform says so or its apply URL (resolved_apply_url
first, then application_url, then source_url) is on the ATS host. Every applier turns that URL into
the canonical form URL itself (apply_url), so a job found on a company careers page
(stripe.com/jobs/search?gh_jid=...) still opens the ATS's own form.

Every success assertion here is PROVISIONAL: review_only never clicks submit, so none has been
checked against a real confirmation page. Refine each from the first approved live submission.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from autoapply.appliers.harness import FormApplier, FormField
from autoapply.models.job import Job

CLOSED_TEXT = re.compile(r"no longer accepting applications|job (is )?(closed|no longer available)|position (has been )?filled"
                         r"|page (you('re| are) looking for )?(was )?not found|this job (posting )?(has )?expired"
                         r"|application submission is unavailable", re.I)


class PostingClosed(LookupError):
    """The posting no longer takes applications. Nothing was sent."""


def job_urls(job: Job) -> list[str]:
    return [u for u in (job.resolved_apply_url, job.application_url, job.source_url) if u]


class AtsApplier(FormApplier):
    channels = ("ats_direct",)
    hosts: tuple[str, ...] = ()
    # A CAPTCHA widget the ATS embeds in every form (Lever: hCaptcha). Seeing it on load is not a
    # block, so opening continues; it is still never solved.
    tolerated_widget: re.Pattern | None = None

    def url_for(self, job: Job) -> str | None:
        """The first of the job's URLs on this ATS (or carrying its job-id parameter)."""
        for u in job_urls(job):
            host = urlsplit(u).netloc.lower()
            if any(host == h or host.endswith("." + h) for h in self.hosts):
                return u
        return None

    def can_handle(self, job: Job) -> bool:
        if "linkedin" in (job.application_url or "").lower():
            return False
        return (job.ats_platform or "") == self.platform or self.url_for(job) is not None

    def apply_url(self, job: Job) -> str:
        raise NotImplementedError

    def open_form(self, bs, job: Job) -> None:
        from autoapply.sources.browser import ChallengeDetected
        try:
            bs.goto(self.apply_url(job))
        except ChallengeDetected as e:
            if not (self.tolerated_widget and self.tolerated_widget.search(str(e))):
                raise
        self.after_open(bs)
        body = bs.page.inner_text("body")[:5000]
        if CLOSED_TEXT.search(body):
            raise PostingClosed(f"{self.platform}: posting closed ({CLOSED_TEXT.search(body).group(0)!r})")

    def after_open(self, bs) -> None:
        """Wait for a client-rendered form."""
        bs.page.wait_for_timeout(2500)

    def read_fields(self, bs) -> list[FormField]:
        # react-select renders a second, unlabelled input next to each combobox ("Select...")
        return [f for f in super().read_fields(bs) if not re.fullmatch(r"select\.*", f.label.strip(), re.I)]


def query_param(url: str, name: str) -> str | None:
    return (parse_qs(urlsplit(url).query).get(name) or [None])[0]
