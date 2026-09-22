"""Best-effort listing photo: scrape an Open Graph / Twitter Card image meta
tag off a listing's own page (following the ``viderestilling`` redirect to
whichever real-estate agent's site actually hosts it, or a Boligsiden page
if it doesn't redirect). Almost every listing site sets one of these tags
for social-link previews, so no site-specific scraping is needed.

Confirmed live 2026-09-22: ``boligsiden.dk/viderestilling/{id}`` is not an
HTTP redirect at all -- it's a 200 OK Next.js page containing a
browser-executed ``<meta http-equiv="refresh" content="1;url=...">``, which
``requests`` never follows (it only follows real 3xx responses). Fetching it
directly always lands on this redirect-stub page, which has no og:image of
its own -- the actual agent page one hop further has to be fetched
explicitly.

Same "never raises" posture as ``fetch_html_page`` and ``resolve/jsonld.py``:
a missing or unreachable photo is an expected outcome for a subset of any
real batch, not a failure worth crashing a run over — the popup just omits
the photo. Uses the existing DuckDB raw-response cache (``screener.db``)
rather than a new cache file, keyed by (source, url) like every other fetch
stage, so re-running the pipeline only fetches new listings' pages.
"""
from __future__ import annotations

import html as html_module
import logging
import re

from screener.db import Database, cache_key
from screener.fetch.page_fetch import fetch_html_page

logger = logging.getLogger(__name__)

_SOURCE = "listing_photo_page"

# Meta tags vary in attribute order across sites (content-before-property is
# just as common as property-before-content), so both are tried in turn
# rather than one regex trying to match either order.
_META_PATTERNS = [
    re.compile(
        r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]*content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](?:og:image|twitter:image)["\']',
        re.IGNORECASE,
    ),
]

_META_REFRESH_RE = re.compile(
    r'<meta[^>]+http-equiv=["\']refresh["\'][^>]*content=["\'][^"\']*url=([^"\']+)["\']',
    re.IGNORECASE,
)


def extract_preview_image_url(html: str) -> str | None:
    for pattern in _META_PATTERNS:
        match = pattern.search(html)
        if match:
            return match.group(1)
    return None


def extract_meta_refresh_url(html: str) -> str | None:
    match = _META_REFRESH_RE.search(html)
    return html_module.unescape(match.group(1)) if match else None


def fetch_listing_photo_url(url: str, *, user_agent: str, db: Database) -> str | None:
    key = cache_key(_SOURCE, url, None)
    html = db.get_cached_response(key)
    if html is None:
        html = fetch_html_page(url, user_agent=user_agent)
        if html is None:
            return None
        redirect_url = extract_meta_refresh_url(html)
        if redirect_url is not None:
            redirected_html = fetch_html_page(redirect_url, user_agent=user_agent)
            if redirected_html is not None:
                html = redirected_html
        db.save_raw_response(source=_SOURCE, url=url, params=None, status_code=200, body=html, key=key)
    return extract_preview_image_url(html)
