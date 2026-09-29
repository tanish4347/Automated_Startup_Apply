"""Playwright-backed pieces: the Darwinbox harvester, and network sniffing for JS-only pages.

Darwinbox sits behind Cloudflare bot management: its JSON API returns 403 to plain HTTP clients
even with the page's cookies. Loading the careers page in Chromium once and calling the same API
with fetch() from inside the page (same origin) works.

A single browser is shared per run; pages are opened and closed per company.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from autoapply.ats.harvesters import HarvestError, Target, _result, parse_dt, text_of
from autoapply.ats.http import USER_AGENT
from autoapply.logging import get_logger
from autoapply.sources.base import SourceResult

log = get_logger(__name__)


class Browser:
    """Lazily started shared Chromium. `max_pages` caps concurrent pages."""

    def __init__(self, max_pages: int = 3, headless: bool = True):
        self._sem = asyncio.Semaphore(max_pages)
        self._headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        async with self._lock:
            if self._context is None:
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(headless=self._headless)
                self._context = await self._browser.new_context(user_agent=USER_AGENT)
        return self._context

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()

    async def sniff(self, url: str, timeout_ms: int = 45000) -> tuple[str, str, list[tuple[str, Any]]]:
        """Load url; return (final_url, page_html, [(response_url, parsed_json), ...]).

        page_html includes same-origin iframes' content.
        """
        ctx = await self._ensure()
        async with self._sem:
            page = await ctx.new_page()
            payloads: list[tuple[str, Any]] = []
            pending: list[asyncio.Task] = []

            async def grab(resp):
                if "json" not in (resp.headers.get("content-type") or ""):
                    return
                try:
                    payloads.append((resp.url, json.loads(await resp.text())))
                except Exception:
                    pass

            page.on("response", lambda r: pending.append(asyncio.ensure_future(grab(r))))
            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except Exception as e:  # slow pages still yield whatever loaded
                log.debug("sniff_goto_incomplete", url=url, error=str(e)[:120])
            await page.wait_for_timeout(1500)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            html = await page.content()
            for frame in page.frames[1:]:
                try:
                    html += "\n" + await frame.content()
                except Exception:
                    pass
            final = page.url
            await page.close()
            return final, html, payloads

    async def evaluate_on(self, url: str, script: str, arg: Any, timeout_ms: int = 60000) -> Any:
        """Open url, then run an async JS function (script) with arg inside the page."""
        ctx = await self._ensure()
        async with self._sem:
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                return await page.evaluate(script, arg)
            finally:
                await page.close()


# ── Darwinbox ──────────────────────────────────────────────────────────────

_DARWINBOX_FETCH_ALL = """async (base) => {
    const all = [];
    for (let page = 1; page <= 50; page++) {
        const r = await fetch(base + '/ms/candidateapi/job/alljobs?companyId=main', {
            method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
            body: JSON.stringify({companyId: 'main', page: page, sort_option: 'new', limit: 50})});
        if (!r.ok) return {error: 'HTTP ' + r.status, jobs: all};
        const d = await r.json();
        const jobs = Array.isArray(d.data) ? d.data : [];
        all.push(...jobs);
        if (jobs.length < 50) break;
    }
    return {jobs: all};
}"""


async def darwinbox(browser: Browser, t: Target) -> list[SourceResult]:
    base = f"https://{t.token}.darwinbox.in"
    res = await browser.evaluate_on(f"{base}/ms/candidatev2/main/careers/allJobs", _DARWINBOX_FETCH_ALL, base)
    if not isinstance(res, dict) or (res.get("error") and not res.get("jobs")):
        raise HarvestError(f"darwinbox api: {res.get('error') if isinstance(res, dict) else res}")
    out = []
    for j in res.get("jobs") or []:
        url = f"{base}/ms/candidatev2/main/careers/jobDetails/{j.get('id')}"
        locs = j.get("officelocations_without_area") or []
        lo, hi = j.get("salary_min"), j.get("salary_max")
        comp = f"{j.get('salary_currency') or ''} {lo} - {hi} {j.get('salary_timeframe') or ''}" if (lo or hi) else None
        jd = j.get("jd") if j.get("jd") and j.get("jd") != "Please enter job description" else None
        out.append(_result(
            t, title=j.get("designation_name") or j.get("title") or "", source_id=j.get("id"),
            source_url=url, application_url=url,
            location=" / ".join(l.replace("\r", "").strip() for l in locs) or j.get("officelocation_show_arr"),
            work_mode="remote" if j.get("is_remote") else "unknown",
            employment_type=j.get("emp_type_name"), description_raw=jd, description_text=text_of(jd) or None,
            compensation_text=comp, posted_date=parse_dt(j.get("posted_on")),
        ))
    return out


BROWSER_HARVESTERS = {"darwinbox": darwinbox}
