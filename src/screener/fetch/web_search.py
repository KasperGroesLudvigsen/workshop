"""Free web-search fallback for M7 — Tavily, not Brave.

Brave Search API dropped its free tier Feb 2026 (card required, then
metered billing). Researched broadly instead: DuckDuckGo's Instant Answer
API isn't general search; Google Custom Search closed to new signups;
Bing Search API was retired by Microsoft Aug 2025; SearXNG is free but
needs self-hosting for reliability; most others require a card. Tavily is
the one genuinely free option — 1,000 credits/month, recurring, no card —
and it's purpose-built for exactly this shape: clean title/url/content
results meant to feed a downstream extraction pipeline, which is exactly
what `resolve/web_discovery.py` does with them.

This is secondary to `fetch/osm_poi.py` (OSM POI tags) — only reached for
businesses OSM genuinely doesn't have mapped. Confirmed live via Tavily's
own docs: ``POST https://api.tavily.com/search``, ``Authorization: Bearer
<key>``, JSON body ``{"query": ..., "max_results": ...}``, response
``{"results": [{"title", "url", "content"}, ...]}``.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from screener.db import Database, cache_key
from screener.fetch._rate_limit import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://api.tavily.com/search"


class TavilyBlockedError(RuntimeError):
    """Raised on a non-200 response (bad key, quota exceeded, etc) — never
    let that look like "no results found", same principle as every other
    fetch client in this codebase."""


class TavilyBudgetExceededError(RuntimeError):
    """Raised when a new (non-cached) search would exceed ``monthly_budget``
    — a proactive circuit breaker against Tavily's free 1,000 credits/month
    tier, checked *before* the network call, not after a real 429."""


Transport = Callable[[str, dict[str, Any], str], "TransportResponse"]


@dataclass
class TransportResponse:
    status_code: int
    text: str
    json_body: Any


def _requests_transport(url: str, body: dict[str, Any], api_key: str) -> TransportResponse:
    resp = requests.post(url, json=body, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
    json_body = None
    try:
        json_body = resp.json()
    except ValueError:
        pass
    return TransportResponse(status_code=resp.status_code, text=resp.text, json_body=json_body)


@dataclass
class WebSearchResult:
    title: str
    url: str
    content: str


class TavilyClient:
    def __init__(
        self,
        api_key: str | None = None,
        db: Database | None = None,
        base_url: str = BASE_URL,
        requests_per_second: float = 1.0,
        transport: Transport = _requests_transport,
        monthly_budget: int | None = None,
    ):
        api_key = api_key or os.environ.get("TAVILY_API_KEY")
        if not api_key:
            raise RuntimeError("TAVILY_API_KEY not set — required for TavilyClient")
        self._api_key = api_key
        self._db = db
        self._base_url = base_url
        self._rate_limiter = RateLimiter(requests_per_second)
        self._transport = transport
        self._monthly_budget = monthly_budget
        # Real network calls only -- never incremented on a cache hit, so a
        # caller (resolve/web_discovery_gaps.py) can tell the two apart
        # without search()'s return type needing to change.
        self.calls_made = 0

    def search(self, query: str, *, max_results: int = 5) -> list[WebSearchResult]:
        body = {"query": query, "max_results": max_results}
        key = cache_key("tavily_search", self._base_url, body) if self._db is not None else None

        if key is not None:
            cached = self._db.get_cached_response_row(key)
            if cached is not None:
                status_code, text = cached
                return self._parse(TransportResponse(status_code, text, _safe_json(text)), query)

        if self._db is not None and self._monthly_budget is not None:
            month_start = datetime.now(timezone.utc).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            used = self._db.count_raw_responses_since("tavily_search", month_start)
            if used >= self._monthly_budget:
                raise TavilyBudgetExceededError(
                    f"tavily_search has used {used}/{self._monthly_budget} calls this month — "
                    f"refusing new search for query={query!r}"
                )

        self._rate_limiter.wait()
        resp = self._transport(self._base_url, body, self._api_key)
        self.calls_made += 1
        if self._db is not None:
            self._db.save_raw_response(
                source="tavily_search", url=self._base_url, params=body,
                status_code=resp.status_code, body=resp.text, key=key,
            )
        return self._parse(resp, query)

    def _parse(self, resp: TransportResponse, query: str) -> list[WebSearchResult]:
        if resp.status_code in (401, 403, 429):
            raise TavilyBlockedError(f"Tavily returned {resp.status_code} for query={query!r}")
        if resp.status_code != 200 or resp.json_body is None:
            raise RuntimeError(f"unexpected Tavily response: status={resp.status_code} body={resp.text[:500]!r}")
        return [
            WebSearchResult(title=r.get("title", ""), url=r["url"], content=r.get("content", ""))
            for r in resp.json_body.get("results", [])
        ]


def _safe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None
