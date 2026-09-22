"""Real implementation of ``resolve/pipeline.py``'s ``PageFetcher`` contract
(``Callable[[str], str | None]``) — plain ``requests.get``, nothing else in
this codebase has ever provided one (every caller so far has passed ``None``
or a test fake). ``resolve/web_discovery.py`` needs a real one to fetch each
Tavily search result's own page.

Never raises: a single unreachable or broken business website is an
expected outcome for a subset of any real batch (the resolution cascade
just falls through to its next step, or drops the candidate), not a
failure worth crashing the run over — same principle already applied to
individual CVR/JSON-LD resolution misses elsewhere in this codebase. No
shared rate limiter here, unlike the project's own API clients: each
request targets a different, uncontrolled third-party domain, so there's
no shared quota to protect.
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


def fetch_html_page(url: str, *, user_agent: str, timeout: float = 15.0) -> str | None:
    try:
        resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=timeout)
    except requests.RequestException as e:
        logger.info("page_fetch: %r failed (%s)", url, e)
        return None
    if resp.status_code != 200:
        logger.info("page_fetch: %r returned %d", url, resp.status_code)
        return None
    return resp.text
