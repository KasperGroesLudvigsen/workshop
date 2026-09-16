"""Bulk business discovery via Erhvervsstyrelsen's CVR system-til-system
access ("cvr-permanent") — additive to, not a replacement for, `fetch/cvr.py`
(cvrapi.dk), which stays as-is for name -> address lookups.

cvrapi.dk is lookup-only: "does a business matching this name exist". It
cannot answer "list every restaurant in postal code 4200", which is exactly
what's needed to actually discover hangout/grocery candidates near a
listing (see docs/HANDOFF.md's M6/M7 gap — nothing today finds candidate
names, only validates ones already in hand). cvr-permanent can.

Confirmed live 2026-09-16: ``http://distribution.virk.dk/cvr-permanent`` —
**plain HTTP, not HTTPS** (HTTPS connection attempts timed out at the TCP
level; this is the official system's own design, not fixable client-side,
but it does mean Basic Auth credentials go over the wire in cleartext).
Free, Basic Auth with a username/password issued by Erhvervsstyrelsen
(``CVR_USERNAME``/``CVR_PASSWORD`` env vars — same "read directly from
os.environ, raise if unset" convention as ``DatafordelerAddressValidator``).
Elasticsearch 6.8, full Query DSL. A live bool query for "active businesses,
branch code prefix 56, postal code 4200" returned 61 real, named, addressed
restaurants/cafés in one request.

Query facts confirmed live:
  - ``virksomhedMetadata.sammensatStatus`` is a ``text`` field (checked via
    ``_mapping``) — a ``term`` query needs the lowercased token
    (``"aktiv"``), not the display string (``"Aktiv"``), since ``term``
    matches the analyzed token, not the original value.
  - ``virksomhedMetadata.nyesteBeliggenhedsadresse.postnummer`` is numeric —
    a ``range`` query works directly for postal ranges, no client-side
    filtering needed (unlike Boligsiden's zip codes, which had no range
    filter at all).
  - CVR's own ``adresseId`` does **not** cross-reference DAR's current
    ``id_lokalId`` (checked live against both ``DAR_Adressepunkt`` and
    ``DAR_Husnummer`` — empty both times, likely a legacy DAR-1.0->2.0 ID
    remap) — so this module only returns raw CVR data; coordinate
    resolution happens in ``resolve/cvr_discovery.py`` via the existing
    text-based ``DatafordelerAddressValidator``, unchanged.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import requests

from screener.db import Database
from screener.fetch._rate_limit import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "http://distribution.virk.dk/cvr-permanent"
_PAGE_SIZE = 200
_MAX_RESULT_WINDOW = 10_000  # Elasticsearch 6.8 default index.max_result_window


class CvrPermanentBlockedError(RuntimeError):
    """Raised on a non-200/500 response. Never let an auth/network problem
    look like "no businesses found" — same principle as every other fetch
    client in this codebase."""


class CvrPermanentResultWindowError(RuntimeError):
    """Raised when a query's declared total exceeds Elasticsearch's default
    result window (10,000) -- narrow the postal ranges or branch codes
    rather than silently missing results past the window."""


Transport = Callable[[str, dict[str, Any], dict[str, str]], "TransportResponse"]


@dataclass
class TransportResponse:
    status_code: int
    text: str
    json_body: Any


def _requests_transport(url: str, body: dict[str, Any], auth: tuple[str, str]) -> TransportResponse:
    resp = requests.post(url, json=body, auth=auth, timeout=30)
    json_body = None
    try:
        json_body = resp.json()
    except ValueError:
        pass
    return TransportResponse(status_code=resp.status_code, text=resp.text, json_body=json_body)


class CvrPermanentClient:
    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        db: Database | None = None,
        base_url: str = BASE_URL,
        requests_per_second: float = 3.0,
        transport: Transport = _requests_transport,
    ):
        username = username or os.environ.get("CVR_USERNAME")
        password = password or os.environ.get("CVR_PASSWORD")
        if not username or not password:
            raise RuntimeError("CVR_USERNAME/CVR_PASSWORD not set — required for CvrPermanentClient")
        self._auth = (username, password)
        self._db = db
        self._base_url = base_url
        self._rate_limiter = RateLimiter(requests_per_second)
        self._transport = transport

    def _search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        self._rate_limiter.wait()
        url = f"{self._base_url}/{index}/_search"
        resp = self._transport(url, body, self._auth)
        if self._db is not None:
            self._db.save_raw_response(
                source=f"cvr_permanent_{index}", url=url, params=body,
                status_code=resp.status_code, body=resp.text,
            )
        if resp.status_code in (401, 403, 429):
            raise CvrPermanentBlockedError(f"cvr-permanent returned {resp.status_code} for body={body}")
        if resp.status_code != 200 or resp.json_body is None:
            raise RuntimeError(f"unexpected cvr-permanent response: status={resp.status_code} body={resp.text[:500]!r}")
        return resp.json_body

    def discover_active_businesses(
        self, *, branch_code_prefixes: list[str], postal_ranges: list[tuple[int, int]],
    ) -> Iterator[dict[str, Any]]:
        """Yield raw ``Vrvirksomhed`` records currently active (status
        "aktiv"), whose latest main branch code starts with one of
        ``branch_code_prefixes``, in one of ``postal_ranges``."""
        query = {
            "bool": {
                "must": [
                    {
                        "bool": {
                            "should": [
                                {"prefix": {"Vrvirksomhed.virksomhedMetadata.nyesteHovedbranche.branchekode": p}}
                                for p in branch_code_prefixes
                            ],
                            "minimum_should_match": 1,
                        }
                    },
                    {
                        "bool": {
                            "should": [
                                {
                                    "range": {
                                        "Vrvirksomhed.virksomhedMetadata.nyesteBeliggenhedsadresse.postnummer": {
                                            "gte": zip_from, "lte": zip_to,
                                        }
                                    }
                                }
                                for zip_from, zip_to in postal_ranges
                            ],
                            "minimum_should_match": 1,
                        }
                    },
                    {"term": {"Vrvirksomhed.virksomhedMetadata.sammensatStatus": "aktiv"}},
                ]
            }
        }
        first = self._search("virksomhed", {"query": query, "from": 0, "size": _PAGE_SIZE})
        total = int(first["hits"]["total"])
        if total > _MAX_RESULT_WINDOW:
            raise CvrPermanentResultWindowError(
                f"query matched {total} businesses, over Elasticsearch's {_MAX_RESULT_WINDOW} "
                "result window — narrow branch_code_prefixes/postal_ranges"
            )
        yield from (h["_source"]["Vrvirksomhed"] for h in first["hits"]["hits"])
        fetched = len(first["hits"]["hits"])
        offset = _PAGE_SIZE
        while fetched < total:
            body = self._search("virksomhed", {"query": query, "from": offset, "size": _PAGE_SIZE})
            hits = body["hits"]["hits"]
            if not hits:
                break
            yield from (h["_source"]["Vrvirksomhed"] for h in hits)
            fetched += len(hits)
            offset += _PAGE_SIZE
