"""Jobs from companies' own ATS boards, for companies the resolver has already identified.

Never guesses or probes: it reads (ats_type, ats_token) from `companies`, as written by
`autoapply ats resolve`, and calls only that board's harvester (autoapply/ats/harvesters.py).
Boards are fetched concurrently (per-host limits in AsyncFetcher), priority / Indian companies
first. Each company's last_crawled_at, last_job_count and crawl_error are updated.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Iterator

from autoapply.ats.harvesters import HTTP_HARVESTERS, HarvestError, Target
from autoapply.ats.http import AsyncFetcher
from autoapply.logging import get_logger
from autoapply.sources.base import BaseSource, SourceResult

log = get_logger(__name__)


class AtsBoardsSource(BaseSource):
    def __init__(self, max_companies: int = 500, concurrency: int = 16, session_factory=None):
        self.max_companies = max_companies
        self.concurrency = concurrency
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "ats_boards"

    def _sessions(self):
        if self._session_factory is not None:
            return self._session_factory
        from autoapply.config import get_settings
        from autoapply.models.base import engine_from_settings, get_session_factory
        s = get_settings()
        return get_session_factory(engine_from_settings(s.db.url, s.db.echo))

    def targets(self, session) -> list[tuple[int, Target]]:
        from autoapply.models.company import Company
        rows = (session.query(Company)
                .filter(Company.ats_type.in_(list(HTTP_HARVESTERS)), Company.ats_token.isnot(None),
                        Company.ats_resolved_at.isnot(None))
                .order_by(Company.priority.desc(), Company.is_india.desc(), Company.last_crawled_at.is_(None).desc(),
                          Company.last_crawled_at)
                .limit(self.max_companies).all())
        return [(c.id, Target(company=c.name, ats_type=c.ats_type, token=c.ats_token, careers_url=c.careers_url))
                for c in rows]

    async def _harvest(self, targets: list[tuple[int, Target]]) -> dict[int, Any]:
        sem = asyncio.Semaphore(self.concurrency)
        out: dict[int, Any] = {}
        async with AsyncFetcher(per_host=4) as f:
            async def one(cid: int, t: Target) -> None:
                async with sem:
                    try:
                        out[cid] = await asyncio.wait_for(HTTP_HARVESTERS[t.ats_type](f, t), timeout=300)
                    except (HarvestError, asyncio.TimeoutError) as e:
                        out[cid] = e
                    except Exception as e:
                        out[cid] = e
            await asyncio.gather(*(one(cid, t) for cid, t in targets))
        return out

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        Session = self._sessions()
        with Session() as session:
            targets = self.targets(session)
        log.info("ats_boards_start", boards=len(targets))
        results = asyncio.run(self._harvest(targets))
        now = datetime.now(timezone.utc)
        with Session() as session:
            from autoapply.models.company import Company
            for cid, t in targets:
                res = results.get(cid)
                c = session.get(Company, cid)
                c.last_crawled_at = now
                if isinstance(res, Exception):
                    c.crawl_error = f"{type(res).__name__}: {res}"[:500]
                else:
                    c.crawl_error, c.last_job_count = None, len(res or [])
            session.commit()
        errors = sum(1 for r in results.values() if isinstance(r, Exception))
        log.info("ats_boards_done", boards=len(targets), errors=errors,
                 jobs=sum(len(r) for r in results.values() if isinstance(r, list)))
        for cid, t in targets:
            res = results.get(cid)
            if isinstance(res, list):
                yield from res
