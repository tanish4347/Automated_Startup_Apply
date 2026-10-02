"""Unstop applier (apply_channel=unstop): 84 in-policy jobs. Harness applier, review_only by default.

Flow (probed read-only 2026-10-02 on 30 in-policy postings):
  GET /internships/<slug>-<id>   the page loads its own /api/public/competition/<id> (captured,
                                 not re-requested): regn_type, regn_url, regn_open
  div#un-register-btn            "Quick Apply". class on_unstop = applies on Unstop. Its click
                                 routes client-side to /competitions/<id>/register.
  /competitions/<id>/register    a multi-step form ("Back" / "Next"): step 1 is First/Last name,
                                 Email, Mobile, Gender (SENSITIVE: parks unless you entered it),
                                 Location, Organisation, user Type, Terms. The later steps and the
                                 final control were not reachable logged out.
External apply: all 30 sampled postings had regn_type=1 (on Unstop). A posting with another
regn_type and a regn_url, a register button without on_unstop, or a click that leaves unstop.com
raises Reroute: the job is re-tagged through the link resolver (company_site or the ATS it points
to), nothing is sent. "Application Closed" on the button (a third of the sample, even with
regn_open=1) is a closed posting.
Login: Unstop shows a "Login" control to logged-out visitors; that raises NeedsLogin. Run
`autoapply browser-login unstop` once.
Multi-step: max_steps=4, next_control = the visible "Next". In review_only a Next that tries to send
anything to unstop.com halts the attempt there (see harness).
Robots: Unstop's robots.txt disallows /competitions/*/register for crawlers. As with Internshala,
this applier acts only for the candidate's own logged-in account, one application at a time within
the daily cap, reaching the form by the posting's own button; it never crawls those paths.

Success assertion (explicit): text "registered successfully" / "successfully registered" /
"application submitted" / "you have successfully applied" AND the URL has left /register, or the
posting's button now reads "Registered" / "Applied". PROVISIONAL until a live submission is observed.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from autoapply.appliers.ats import PostingClosed
from autoapply.appliers.harness import FormApplier, FormField, NeedsLogin, Reroute
from autoapply.models.job import Job

SUCCESS_TEXT = re.compile(r"registered successfully|successfully registered|application (has been )?submitted"
                          r"|you have successfully applied|applied successfully", re.I)
CLOSED_BUTTON = re.compile(r"closed|expired|ended", re.I)
DONE_BUTTON = re.compile(r"^\s*(registered|applied)\b", re.I)
CHROME_LABELS = re.compile(r"^(search|stay in the loop)", re.I)


def competition_of(captured) -> dict:
    data = (captured or {}).get("data") if isinstance(captured, dict) else None
    return (data or {}).get("competition") or data or {}


class UnstopApplier(FormApplier):
    platform = "unstop"
    channels = ("unstop",)
    max_steps = 4

    def open_form(self, bs, job: Job) -> None:
        comp = competition_of(bs.goto(job.source_url, capture_response="/api/public/competition/"))
        regn_url = comp.get("regn_url")
        if comp.get("regn_type") not in (None, 1) and regn_url and "unstop.com" not in regn_url:
            raise Reroute(regn_url, f"Unstop posting applies externally (regn_type={comp.get('regn_type')}): {regn_url}")
        bs.page.wait_for_timeout(1500)
        btn = bs.page.locator("#un-register-btn").first
        if btn.count() == 0:
            raise PostingClosed("unstop: no register button on the posting")
        text = btn.inner_text().strip()
        if CLOSED_BUTTON.search(text):
            raise PostingClosed(f"unstop: posting says {text!r}")
        if DONE_BUTTON.search(text):
            raise PostingClosed(f"unstop: already {text.lower()} on this posting")
        if "on_unstop" not in (btn.get_attribute("class") or ""):
            target = btn.get_attribute("href") or regn_url
            if target and "unstop.com" not in target:
                raise Reroute(target, f"Unstop register button leads off-platform: {target}")
        if bs.page.get_by_text(re.compile(r"^\s*login\s*$", re.I)).filter(visible=True).count():
            raise NeedsLogin("Unstop is not logged in. Run `autoapply browser-login unstop`, sign in, close the window.")
        bs.page.keyboard.press("Escape")
        btn.click()
        bs.page.wait_for_timeout(4000)
        host = urlsplit(bs.page.url).netloc
        if host and not host.endswith("unstop.com"):
            raise Reroute(bs.page.url, f"Unstop register click left the platform for {bs.page.url}")

    def read_fields(self, bs) -> list[FormField]:
        return [f for f in super().read_fields(bs) if not CHROME_LABELS.search(f.label)]

    def _button(self, bs, pattern: str):
        return bs.page.get_by_role("button", name=re.compile(pattern, re.I)).filter(visible=True).first

    def next_control(self, bs):
        nxt = self._button(bs, r"^\s*next\s*$")
        return nxt if nxt.count() else None

    def submit_control(self, bs):
        return self._button(bs, r"^\s*(submit|register|apply|finish)( now)?\s*$")

    def success_assertion(self, bs, before_url: str) -> str | None:
        text = bs.page.inner_text("body")[:20000]
        m = SUCCESS_TEXT.search(text)
        if m and "/register" not in urlsplit(bs.page.url).path:
            return f"confirmation text {m.group(0)!r} and URL moved to {bs.page.url}"
        btn = bs.page.locator("#un-register-btn").first
        if btn.count() and DONE_BUTTON.search(btn.inner_text()):
            return f"posting button now reads {btn.inner_text().strip()!r}"
        return None
