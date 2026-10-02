"""Lever applier (harness; see autoapply/appliers/ats.py).

Form URL: https://jobs.lever.co/<company>/<posting id>/apply (checked read-only 2026-10-02 on
job 17166, Stand Together): server-rendered form#application-form, 14 fields. Standard fields
(resume, name, email, phone, location, current company, URLs) carry their own labels; custom
questions are "cards" whose <input>s have no label of their own: their question text is the
.application-label of the enclosing li.application-question (LABELS_JS reads it).
Lever renders an hCaptcha checkbox widget into the form on load. It is detected and never
solved: it is not treated as a block on opening the form (it is on every Lever form, and nothing
is sent while filling); in live mode, a challenge on submit means the success assertion fails
and the attempt is uncertain.

Submit: button#btn-submit "Submit application".
Success assertion (explicit): the URL moves to /<company>/<id>/thanks, or the form is gone and
the page says "Application submitted". PROVISIONAL until a live submission is observed.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from autoapply.appliers.ats import AtsApplier
from autoapply.models.job import Job

SUCCESS_TEXT = re.compile(r"application submitted|thanks? for applying|we('ve| have) received your application", re.I)

# data-aa -> the question text of its li.application-question (cards have no <label for>)
LABELS_JS = r"""() => { const out = {};
  for (const q of document.querySelectorAll('li.application-question')) {
    const l = q.querySelector('.application-label'); const t = l ? l.innerText.replace(/\s+/g, ' ').trim() : '';
    if (!t) continue;
    for (const el of q.querySelectorAll('[data-aa]')) out[el.getAttribute('data-aa')] = t;
  } return out; }"""


class LeverApplier(AtsApplier):
    platform = "lever"
    hosts = ("lever.co",)
    tolerated_widget = re.compile(r"hcaptcha", re.I)

    def apply_url(self, job: Job) -> str:
        url = self.url_for(job) or ""
        m = re.match(r"/([^/]+)/([0-9a-f-]{36})", urlsplit(url).path)
        if not m:
            raise LookupError(f"lever: no company / posting id in {url!r}")
        host = urlsplit(url).netloc or "jobs.lever.co"
        return f"https://{host}/{m.group(1)}/{m.group(2)}/apply"

    def label_overrides(self, bs) -> dict[str, str]:
        return bs.page.evaluate(LABELS_JS)

    def submit_control(self, bs):
        return bs.page.locator("#btn-submit").first

    def success_assertion(self, bs, before_url: str) -> str | None:
        url = bs.page.url
        if urlsplit(url).path.rstrip("/").endswith("/thanks"):
            return f"URL moved to {url}"
        m = SUCCESS_TEXT.search(bs.page.inner_text("body")[:20000])
        if m and bs.page.locator("#application-form").count() == 0:
            return f"form gone and confirmation text {m.group(0)!r} at {url}"
        return None
