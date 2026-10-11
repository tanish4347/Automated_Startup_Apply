"""SmartRecruiters applier (harness; see autoapply/appliers/ats.py).

Checked read-only 2026-10-02 on job 20289 (InfiniteQuant): the posting page
jobs.smartrecruiters.com/<company>/<id> carries a#st-apply "I'm interested", a plain link to the
one-click apply app (jobs.smartrecruiters.com/oneclick-ui/company/<id>/publication/<uuid>). The
applier follows that href (a GET; the same navigation the click makes). The apply app is built from
web components. Generic discovery uses Playwright's native locators across open shadow roots,
frames, and ARIA controls; a DataDome challenge raises ChallengeDetected and is never solved.

Submit: the apply app's submit button (button[type=submit] / "Submit").
Success assertion (explicit): the apply app moves to a success/confirmation route, or the page
says "Thank you for applying" / "Your application has been submitted" and the submit button is
gone. PROVISIONAL (the weakest of the four: the apply app's success screen has never been seen).
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from autoapply.appliers.ats import AtsApplier, PostingClosed
from autoapply.models.job import Job

SUCCESS_TEXT = re.compile(r"thank you for (applying|your application)|application (has been )?(submitted|sent|received)", re.I)


class SmartRecruitersApplier(AtsApplier):
    platform = "smartrecruiters"
    hosts = ("smartrecruiters.com",)
    max_steps = 5

    def apply_url(self, job: Job) -> str:
        url = self.url_for(job)
        if not url:
            raise LookupError("smartrecruiters: no posting URL")
        return url

    def open_form(self, bs, job: Job) -> None:
        url = self.apply_url(job)
        if "/oneclick-ui/" not in url:
            bs.goto(url)
            link = bs.page.locator("a#st-apply, a.js-oneclick").first
            if link.count() == 0:
                raise PostingClosed("smartrecruiters: no \"I'm interested\" link on the posting (closed?)")
            url = urljoin(bs.page.url, link.get_attribute("href") or "")
        bs.goto(url)
        # The harness waits for meaningful controls with bounded stabilization polling.

    def next_control(self, bs):
        # Match intermediate step buttons
        btn = bs.page.locator("button, a").filter(has_text=re.compile(r"^\s*(next|continue|add information|review)\s*$", re.I)).first
        return btn if btn.count() > 0 else None

    def submit_control(self, bs):
        # The final submission button
        return bs.page.locator("button").filter(has_text=re.compile(r"^\s*(submit|send application)\s*$", re.I)).first

    def success_assertion(self, bs, before_url: str) -> str | None:
        path = urlsplit(bs.page.url).path
        if path != urlsplit(before_url).path and re.search(r"success|confirm|thank", path, re.I):
            return f"apply app moved to {bs.page.url}"
        m = SUCCESS_TEXT.search(bs.page.inner_text("body")[:20000])
        if m and self.submit_control(bs).count() == 0:
            return f"submit gone and confirmation text {m.group(0)!r} at {bs.page.url}"
        return None
