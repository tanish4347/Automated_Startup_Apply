"""Internshala applier (apply_channel=internshala): 332 of the 554 in-policy jobs.

Runs inside the harness (autoapply/appliers/harness.py): review_only by default, the answer
engine fills fields, the active resume is uploaded, the page is screenshotted, and the attempt
parks at the submit control for /review. Free-text prompts ("Why should you be hired for this
role?", availability, assignment questions) park in this phase; answers given in /review write
back so the same question resolves at tier 1 next time.

Flow (posting page -> application form):
  GET /internship/detail/<slug>           (public; allowed by robots.txt)
  its "Apply now" link -> /student/interstitial/application/<slug>   (needs the login session)
  -> the application form page
Robots: Internshala's robots.txt disallows /student/* and /application/* for crawlers. This
applier only acts on the candidate's own logged-in account, one application at a time, within
the daily cap; it never crawls those paths.

Success assertion (explicit; anything short of it is `uncertain`, never `submitted`):
  after the submit click, the page must show Internshala's own confirmation: text matching
  SUCCESS_TEXT ("Application submitted" / "You have successfully applied" / "Applied successfully")
  inside the page, AND the URL must have left the application form. PROVISIONAL until a live
  submission is observed: review_only never clicks submit, so this cannot be checked against a
  real confirmation page yet. Refine it from the first approved live submission's evidence.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from autoapply.appliers.harness import FormApplier, FormField
from autoapply.logging import get_logger
from autoapply.models.job import Job

log = get_logger(__name__)

BASE = "https://internshala.com"
SUCCESS_TEXT = re.compile(r"application (has been )?submitted|you have successfully applied|applied successfully", re.I)
LOGIN_TEXT = re.compile(r"login\s*/\s*register|login with google|register to apply|login to apply", re.I)
# Controls that belong to Internshala's page chrome, not to the application form.
CHROME_LABELS = re.compile(r"^(search|subscribe|email address for job alerts|enter your email)", re.I)


class NeedsLogin(Exception):
    pass


class InternshalaApplier(FormApplier):
    platform = "internshala"
    channels = ("internshala",)

    def apply_link(self, bs, job: Job) -> str:
        bs.goto(job.source_url)
        link = bs.page.locator("a.apply_now_button, #apply_now_button, a[href*='/interstitial/application/']").first
        if link.count() == 0:
            raise LookupError("no Apply link on the posting (closed, or already applied)")
        href = link.get_attribute("href") or ""
        url = urljoin(BASE, href.split("?", 1)[0])   # no tracking query (robots: no "?" URLs)
        return url

    def open_form(self, bs, job: Job) -> None:
        bs.goto(self.apply_link(bs, job))
        if "/registration/" in bs.page.url or "/login" in bs.page.url or LOGIN_TEXT.search(bs.page.inner_text("body")[:3000]):
            raise NeedsLogin("Internshala is not logged in. Run `autoapply browser-login internshala`, sign in, "
                             "confirm you see your dashboard, close the window.")

    def read_fields(self, bs) -> list[FormField]:
        return [f for f in super().read_fields(bs) if not CHROME_LABELS.search(f.label)]

    def submit_control(self, bs):
        return bs.page.get_by_role("button", name=re.compile(r"^\s*submit", re.I)).first

    def success_assertion(self, bs, before_url: str) -> str | None:
        text = bs.page.inner_text("body")[:20000]
        m = SUCCESS_TEXT.search(text)
        left_form = urlsplit(bs.page.url).path != urlsplit(before_url).path
        if m and left_form:
            return f"confirmation text {m.group(0)!r} and URL moved to {bs.page.url}"
        return None


# ── reachability (public posting pages only) ─────────────────────────────────

def reachability(session, limit: int | None = None) -> dict[str, Any]:
    """Of the in-policy active Internshala jobs, how many postings are live with an Apply link.
    Fetches only /internship/detail/<slug> (robots-allowed), at Internshala's pace."""
    from collections import Counter
    from autoapply.sources.http_client import HttpClient
    jobs = (session.query(Job).filter(Job.apply_channel == "internshala", Job.is_active == 1, Job.location_fit == "ok")
            .order_by(Job.id).all())[:limit]
    counts: Counter = Counter()
    examples: dict[str, str] = {}
    with HttpClient(requests_per_second=2.0) as http:
        for job in jobs:
            url = (job.source_url or "").split("?", 1)[0]
            resp = http.get(url) if url.startswith(f"{BASE}/internship/detail/") else None
            if resp is None or resp.status_code != 200:
                kind = f"unreachable ({getattr(resp, 'status_code', 'no response')})"
            elif "/internship/detail/" not in str(resp.url):
                kind = "posting removed (redirected away)"
            else:
                html = resp.text
                if re.search(r"apply_now_button|/interstitial/application/", html):
                    kind = "reachable: Apply link present"
                elif re.search(r"applications? (are )?closed|no longer accepting|internship (has )?expired", html, re.I):
                    kind = "closed"
                else:
                    kind = "live, no Apply link"
            counts[kind] += 1
            examples.setdefault(kind, url)
    return {"total": len(jobs), "counts": dict(counts), "examples": examples}
