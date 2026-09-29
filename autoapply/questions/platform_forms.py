"""Capture the application form of the job platforms that carry most of the volume (Internshala,
Unstop, Naukri, Instahyre), through the browser tier's logged-in profiles. NEVER submits:

- The session is put in read-only mode first (BrowserSession.read_only): every non-GET request
  is aborted inside the browser. Clicking "Apply" can open a form (a GET navigation) but cannot
  send an application. Blocked requests are reported, so a platform whose apply button is
  itself the submission shows up as exactly that.
- Only the apply control is clicked; the form is then read, never filled.
- A login form or login redirect stops the platform with NeedsLogin: run
  `autoapply browser-login <platform>` once, then harvest again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin
from typing import Any

from sqlalchemy.orm import Session

from autoapply.logging import get_logger
from autoapply.questions.harvest import HarvestedField, HarvestedForm, clean_label

log = get_logger(__name__)


class NeedsLogin(Exception):
    pass


@dataclass
class PlatformSpec:
    platform: str
    apply_text: str          # regex for the apply control's visible text
    source: str              # jobs.source whose postings are opened


PLATFORMS = {
    "internshala": PlatformSpec("internshala", r"^\s*apply( now)?\s*$", "internshala"),
    "unstop": PlatformSpec("unstop", r"^\s*(register( now)?|apply( now)?|quick apply)\s*$", "unstop"),
    "naukri": PlatformSpec("naukri", r"^\s*(apply|apply on company site)\s*$", "naukri"),
    "instahyre": PlatformSpec("instahyre", r"^\s*(apply|i'?m interested|view job)\s*$", "instahyre"),
}

# Reads every visible form control on the page (and open dialogs), with its label, type,
# required flag and options. Radio/checkbox groups become one select / multi_select.
FORM_DUMP_JS = r"""() => {
  const vis = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const text = el => (el && el.innerText || '').replace(/\s+/g, ' ').trim();
  const nearText = el => {
    let c = el.parentElement;
    for (let i = 0; i < 5 && c; i++, c = c.parentElement) {
      const cands = [...c.querySelectorAll('label,legend,h1,h2,h3,h4,h5,h6,p,span,div')]
        .filter(x => !x.contains(el) && !x.querySelector('input,textarea,select') && text(x) && text(x).length < 300
                && (x.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING));
      if (cands.length) return text(cands[cands.length - 1]);
    }
    return '';
  };
  const labelOf = el => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (text(l)) return text(l); }
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const by = el.getAttribute('aria-labelledby');
    if (by) { const t = by.split(/\s+/).map(i => text(document.getElementById(i))).join(' ').trim(); if (t) return t; }
    const wrap = el.closest('label'); if (text(wrap)) return text(wrap);
    return nearText(el) || el.getAttribute('placeholder') || el.name || '';
  };
  const groupLabel = el => { const fs = el.closest('fieldset'); const lg = fs && fs.querySelector('legend');
    return text(lg) || nearText(el.closest('label') || el) || el.name || ''; };
  const optText = el => { if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (text(l)) return text(l); }
    return text(el.closest('label')) || el.value || ''; };
  const out = [], groups = {};
  for (const el of document.querySelectorAll('input,textarea,select,[contenteditable="true"]')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden','submit','button','image','reset','search','password'].includes(type)) continue;
    if (!vis(el) && !['radio','checkbox','file'].includes(type)) continue;
    const required = el.required || el.getAttribute('aria-required') === 'true';
    if (type === 'radio' || type === 'checkbox') {
      // One question per input name. Its label is the text just before the smallest element that
      // holds every option of the group (not the text before each option, which is the previous option).
      const key = el.name || groupLabel(el);
      if (!groups[key]) {
        const members = el.name ? [...document.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`)] : [el];
        let box = el.parentElement;
        while (box && !members.every(m => box.contains(m))) box = box.parentElement;
        const fs = el.closest('fieldset'), lg = fs && fs.querySelector('legend');
        groups[key] = {label: text(lg) || (box ? nearText(box) : '') || el.name || '',
                       field_type: type === 'radio' ? 'select' : 'multi_select', required: false, options: []};
      }
      groups[key].options.push(optText(el)); groups[key].required = groups[key].required || required; continue;
    }
    const tag = el.tagName;
    const field_type = tag === 'SELECT' ? 'select' : (tag === 'TEXTAREA' || el.isContentEditable) ? 'textarea'
      : ({file: 'file', email: 'email', tel: 'phone', date: 'date', number: 'number', url: 'url'}[type] || 'text');
    const label = labelOf(el);
    out.push({label, field_type, required: required || /\*\s*$/.test(label),
              options: tag === 'SELECT' ? [...el.options].map(o => o.text.trim()).filter(Boolean) : null});
  }
  for (const g of Object.values(groups)) out.push(g);
  return {url: location.href, title: document.title,
          login: !!document.querySelector('input[type=password]') || /\/(login|signin|sign-in|nlogin)\b/i.test(location.pathname),
          fields: out};
}"""


def sample_job_url(session: Session, source: str) -> tuple[int, str, str | None] | None:
    """One active, accepted posting of the platform (the kind we will actually apply to)."""
    from autoapply.models.job import Job
    job = (session.query(Job).filter(Job.source == source, Job.is_active == 1, Job.source_url.isnot(None))
           .order_by(Job.classification_status.desc(), Job.discovered_at.desc()).first())
    return (job.id, job.source_url, job.company) if job else None


def fields_from_dump(dump: dict[str, Any]) -> list[HarvestedField]:
    out = []
    for f in dump.get("fields") or []:
        label = clean_label(f.get("label"))
        if not label:
            continue
        out.append(HarvestedField(label, f.get("field_type") or "text", bool(f.get("required")),
                                  [o for o in f.get("options") or [] if o] or None, section="platform"))
    return out


def harvest_platform(session: Session, platform: str, browser_session=None) -> HarvestedForm:
    """Open one real application form on the platform, dump it, submit nothing."""
    from autoapply.sources.browser import BrowserSession
    spec = PLATFORMS[platform]
    picked = sample_job_url(session, spec.source)
    if picked is None:
        raise LookupError(f"no active {platform} job in the DB to open")
    job_id, url, company = picked
    own = browser_session is None
    s = browser_session or BrowserSession(platform).__enter__()
    try:
        s.read_only()
        s.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        s.page.wait_for_timeout(2500)
        button = s.page.get_by_role("button", name=re.compile(spec.apply_text, re.I)).or_(
            s.page.get_by_role("link", name=re.compile(spec.apply_text, re.I))).first
        if button.count() == 0:  # styled divs/spans without a button role: find by visible text
            button = s.page.get_by_text(re.compile(spec.apply_text, re.I)).first
        if button.count() == 0:
            prompt = s.page.get_by_text(re.compile(r"(log ?in|sign ?in|register) to (apply|continue)", re.I))
            if prompt.count() or s.page.evaluate(FORM_DUMP_JS).get("login"):
                raise NeedsLogin(f"{platform} shows a login prompt instead of an apply button. Run "
                                 f"`autoapply browser-login {platform}` once, then harvest again.")
            raise LookupError(f"{platform}: no apply control on {url} (page title: {s.page.title()!r})")
        href = button.get_attribute("href")
        if href and not href.startswith(("#", "javascript")):
            # A plain link: open it directly (the same GET the click makes); popups can't intercept it.
            s.page.goto(urljoin(s.page.url, href), wait_until="domcontentloaded", timeout=45000)
        else:
            s.page.keyboard.press("Escape")  # dismiss newsletter/cookie modals covering the button
            button.click(timeout=10000)
        s.page.wait_for_timeout(4000)
        dump = s.page.evaluate(FORM_DUMP_JS)
        if dump.get("login"):
            raise NeedsLogin(f"{platform} asks for a login. Run `autoapply browser-login {platform}` once, "
                             "sign in by hand, close the window, then harvest again.")
        blocked = getattr(s, "blocked", [])
        form = HarvestedForm(platform, platform, company, dump.get("url") or url, fields_from_dump(dump), job_id=job_id)
        log.info("platform_form_captured", platform=platform, fields=len(form.fields), url=form.url,
                 blocked_requests=len(blocked))
        if not form.fields and blocked:
            log.warning("platform_apply_is_a_submit", platform=platform, blocked=blocked[:3],
                        note="clicking apply tried to send a request (blocked); there is no form to capture")
        return form
    finally:
        if own:
            s.__exit__(None, None, None)


# ── SmartRecruiters (browser: the form config is behind DataDome for plain HTTP) ─────────────

_SR_LABELS = {"firstAndLastName": "First and last name", "email": "Email", "placeOfResidence": "Place of residence",
              "phoneNumber": "Phone number", "experience": "Experience", "education": "Education",
              "resume": "Resume", "messageToHiringManager": "Message to the hiring manager", "linkedIn": "LinkedIn profile",
              "website": "Website", "facebook": "Facebook profile", "x": "X (Twitter) profile",
              "institution": "Education institution", "educationDates": "Education dates"}
_SR_TYPES = {"resume": "file", "email": "email", "phoneNumber": "phone", "messageToHiringManager": "textarea",
             "placeOfResidence": "location"}
_SR_SKIP = {"easyApply", "socialProfiles"}   # containers / one-click import buttons, not questions


def parse_smartrecruiters_config(config: dict[str, Any]) -> list[HarvestedField]:
    out: list[HarvestedField] = []

    def walk(name: str, node: dict[str, Any]) -> None:
        if not isinstance(node, dict) or not node.get("visible", True):
            return
        if name not in _SR_SKIP and name.startswith("easyApply") is False:
            out.append(HarvestedField(_SR_LABELS.get(name, name), _SR_TYPES.get(name, "text"), bool(node.get("required")),
                                      section="system"))
        for k, v in node.items():
            if isinstance(v, dict) and "visible" in v:
                walk(k, v)

    for name, node in (config.get("fieldSets") or {}).items():
        walk(name, node)
    return out


def harvest_smartrecruiters(tokens: list[tuple[str, str | None]], jobs_per_company: int = 1) -> list[HarvestedForm]:
    """Open each company's one-click apply page read-only and keep the /config it loads."""
    import httpx
    from autoapply.questions.harvest import pick_jobs
    from autoapply.sources.browser import BrowserSession
    forms: list[HarvestedForm] = []
    api = httpx.Client(timeout=20)
    with BrowserSession("smartrecruiters_forms") as s:
        s.read_only()
        for token, name in tokens:
            try:
                data = api.get(f"https://api.smartrecruiters.com/v1/companies/{token}/postings", params={"limit": 50}).json()
                jobs = [(j["id"], j.get("name") or "") for j in data.get("content") or []]
                for jid in pick_jobs(jobs, jobs_per_company):
                    det = api.get(f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{jid}").json()
                    ident = (det.get("company") or {}).get("identifier") or token
                    url = f"https://jobs.smartrecruiters.com/oneclick-ui/company/{ident}/publication/{det['uuid']}?dcr_ci={ident}"
                    config = s.goto(url, capture_response="/config")
                    fields = parse_smartrecruiters_config(config or {})
                    if fields:
                        forms.append(HarvestedForm("smartrecruiters", token.lower(), name, url, fields))
            except Exception as e:
                log.warning("question_harvest_failed", platform="smartrecruiters", token=token, error=str(e)[:120])
    return forms
