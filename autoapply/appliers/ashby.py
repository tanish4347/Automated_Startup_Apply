"""Ashby applier (harness; see autoapply/appliers/ats.py).

Form URL: https://jobs.ashbyhq.com/<org>/<posting id>/application. Checked read-only 2026-10-02 on
job 18221 (OnePay "AI Research Intern"): the page builds the form from two GraphQL QUERIES sent as
POSTs (non-user-graphql?op=ApiJobPosting, ?op=ApiOrganizationFromHostedJobsPageName). Plain
read-only mode aborts them and the page shows "Application submission is unavailable", so this
applier allows exactly those requests (read_only_allow): POSTs to /api/non-user-graphql whose body
is a GraphQL `query`. A `mutation` (the submission) is still aborted. With them, 17 fields render.

Yes/No questions are pairs of buttons (.ashby-application-form-input-yesno-option), not inputs;
YESNO_JS marks each group so it is read as a select and filled by clicking the option.
Submit: button.ashby-application-form-submit-button.
Success assertion (explicit): Ashby's success container (.ashby-application-form-success-container)
is shown, or the form is gone and the page says "Thank you for applying" / "Application submitted".
PROVISIONAL until a live submission is observed.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from autoapply.appliers.ats import AtsApplier
from autoapply.appliers.harness import FormField
from autoapply.models.job import Job

SUCCESS_TEXT = re.compile(r"thank(s| you) for applying|application (was |has been )?(submitted|received)", re.I)

YESNO_JS = r"""() => { const out = []; let n = 0;
  for (const entry of document.querySelectorAll('.ashby-application-form-field-entry')) {
    const opts = [...entry.querySelectorAll('.ashby-application-form-input-yesno-option')];
    if (!opts.length) continue;
    const l = entry.querySelector('label'); const label = (l ? l.innerText : '').replace(/\s+/g, ' ').trim();
    const ids = opts.map(o => { const id = 'yn' + (n++); o.setAttribute('data-aa', id); return id; });
    out.push({label, options: opts.map(o => o.innerText.trim()), option_aa: ids,
              required: /\*\s*$/.test(label) || !!entry.querySelector('[class*=required]')});
  } return out; }"""


def graphql_read_only(method: str, url: str, body: str) -> bool:
    """Allow Ashby's GraphQL reads; refuse everything else (mutations included)."""
    if method != "POST" or "/api/non-user-graphql" not in url or urlsplit(url).netloc != "jobs.ashbyhq.com":
        return False
    try:
        query = json.loads(body).get("query") or ""
    except (ValueError, AttributeError):
        return False
    return query.lstrip().startswith("query") and "mutation" not in query


class AshbyApplier(AtsApplier):
    platform = "ashby"
    hosts = ("ashbyhq.com",)
    read_only_allow = staticmethod(graphql_read_only)

    def apply_url(self, job: Job) -> str:
        url = self.url_for(job) or ""
        m = re.match(r"/([^/]+)/([0-9a-f-]{36})", urlsplit(url).path)
        if not m:
            raise LookupError(f"ashby: no organisation / posting id in {url!r}")
        return f"https://jobs.ashbyhq.com/{m.group(1)}/{m.group(2)}/application"

    def after_open(self, bs) -> None:
        bs.page.wait_for_timeout(4000)

    def read_fields(self, bs) -> list[FormField]:
        from autoapply.questions.harvest import clean_label
        fields = super().read_fields(bs)
        for g in bs.page.evaluate(YESNO_JS):
            if clean_label(g["label"]):
                fields.append(FormField(clean_label(g["label"]), "select", bool(g["required"]), g["options"],
                                        g["option_aa"][0], "yesno", g["option_aa"]))
        return fields

    def fill(self, bs, field: FormField, value) -> None:
        if field.control == "yesno":
            for opt, aa in zip(field.options or [], field.option_aa or []):
                if opt.strip().lower() == str(value).strip().lower():
                    bs.page.locator(f'[data-aa="{aa}"]').click()
            return
        super().fill(bs, field, value)

    def submit_control(self, bs):
        return bs.page.locator("button.ashby-application-form-submit-button").first

    def success_assertion(self, bs, before_url: str) -> str | None:
        if bs.page.locator(".ashby-application-form-success-container").count():
            return f"Ashby success container shown at {bs.page.url}"
        m = SUCCESS_TEXT.search(bs.page.inner_text("body")[:20000])
        if m and bs.page.locator("button.ashby-application-form-submit-button").count() == 0:
            return f"form gone and confirmation text {m.group(0)!r} at {bs.page.url}"
        return None
