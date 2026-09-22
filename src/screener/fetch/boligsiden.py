"""Boligsiden fetch client (stage 1 — raw acquisition only, no scoring logic).

Replaces the earlier Boliga-based client. Boliga's API sits behind
Cloudflare and needs browser-impersonating transport (``curl_cffi``) to get
past it at all, and even then real runs in this project hit repeated
silent connection timeouts. Researching two known Danish open-source
projects (Dan Saattrup Smart's ``bolig-ping``, Mikkel Krogsholm's
``api-mapper``) turned up a better source: ``bolig-ping`` queries
Boligsiden's own API, ``api.boligsiden.dk/search/cases``, with a plain
``requests.get()`` — no headers, no impersonation.

Confirmed live 2026-09-15 via plain ``curl``: clean 200s, no Cloudflare
challenge, ``per_page`` up to 500 works, and the response already carries
lat/lon (WGS84, no reprojection needed) and a clean ``lotArea`` field —
better than any candidate Boliga's response ever gave us. The valid
``addressTypes`` enum (confirmed via the API's own 400 error body, which
lists every allowed value) includes ``"holiday house"`` — fritidshus's
equivalent here.

There is no server-side postal-code *range* filter, only an exact-match
``zipCodes`` list (each entry needing a real 4-digit code) — impractical
for the ~1000 codes in this project's postal ranges. Filtering to those
ranges happens client-side in :func:`fetch_postal_ranges`, using the
``zipCode`` every case already carries. Boligsiden also has no small
per-query result cap the way Boliga did (300, needing recursive postal-code
bisection) — a plain, un-sharded page loop covers the whole country.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import requests

from screener.config import BoligsidenSettings
from screener.db import Database
from screener.fetch._rate_limit import RateLimiter

logger = logging.getLogger(__name__)

_DEFAULT_PER_PAGE = 500


class BoligsidenBlockedError(RuntimeError):
    """Raised on HTTP 403/429. Never silently degrade a block into an empty
    result set — unobserved against this API so far, but the same
    principle applies as it did for Boliga."""


class BoligsidenTruncatedResultsError(RuntimeError):
    """Raised when the number of cases actually collected across all pages
    doesn't match the ``totalHits`` the API declared on page 1."""


Transport = Callable[[str, dict[str, Any], dict[str, str]], "TransportResponse"]


@dataclass
class TransportResponse:
    status_code: int
    text: str
    json_body: Any


def _requests_transport(url: str, params: dict[str, Any], headers: dict[str, str]) -> TransportResponse:
    resp = requests.get(url, params=params, headers=headers, timeout=20)
    body = None
    try:
        body = resp.json()
    except ValueError:
        pass
    return TransportResponse(status_code=resp.status_code, text=resp.text, json_body=body)


class BoligsidenClient:
    def __init__(
        self,
        settings: BoligsidenSettings,
        db: Database | None = None,
        transport: Transport = _requests_transport,
    ):
        self._settings = settings
        self._db = db
        self._transport = transport
        self._rate_limiter = RateLimiter(settings.requests_per_second)

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self._settings.user_agent, "Accept": "application/json"}

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        self._rate_limiter.wait()
        resp = self._transport(self._settings.base_url, params, self._headers())
        if self._db is not None:
            self._db.save_raw_response(
                source="boligsiden_search",
                url=self._settings.base_url,
                params=params,
                status_code=resp.status_code,
                body=resp.text,
            )
        if resp.status_code in (403, 429):
            raise BoligsidenBlockedError(
                f"boligsiden returned {resp.status_code} for params={params} — treat as a "
                "hard failure, not an empty result set"
            )
        if resp.status_code != 200 or resp.json_body is None:
            raise RuntimeError(f"unexpected boligsiden response: status={resp.status_code} body={resp.text[:500]!r}")
        return resp.json_body

    def search_page(self, *, address_type: str, page: int, per_page: int = _DEFAULT_PER_PAGE) -> dict[str, Any]:
        return self._get({"addressTypes": address_type, "page": page, "per_page": per_page})

    def fetch_postal_ranges(
        self,
        *,
        address_type: str,
        postal_ranges: list[tuple[int, int]],
        per_page: int = _DEFAULT_PER_PAGE,
    ) -> Iterator[dict[str, Any]]:
        """Page through *every* national result for ``address_type`` (no
        postal filter server-side — see module docstring), yielding only
        cases whose zip code falls in ``postal_ranges``. Asserts the total
        number of raw cases collected matches the API's declared
        ``totalHits`` rather than trusting page-count arithmetic, and warns
        (does not silently drop) on an unexpected duplicate ``caseID``."""
        first_page = self.search_page(address_type=address_type, page=1, per_page=per_page)
        total = int(first_page["totalHits"])
        num_pages = (total + per_page - 1) // per_page if total else 0

        seen_ids: set[str] = set()
        collected = 0

        def handle_page(body: dict[str, Any]) -> Iterator[dict[str, Any]]:
            nonlocal collected
            for case in body["cases"]:
                case_id = case["caseID"]
                if case_id in seen_ids:
                    # Confirmed live at national scale: the underlying sort
                    # order can shift between two of the ~15 sequential page
                    # requests this takes, so the same case legitimately
                    # appears twice. Not a truncation risk -- just don't
                    # double-count it.
                    logger.warning("boligsiden returned duplicate caseID %s across pages", case_id)
                    continue
                seen_ids.add(case_id)
                collected += 1
                if _in_postal_ranges(case["address"]["zipCode"], postal_ranges):
                    yield case

        yield from handle_page(first_page)
        for page in range(2, num_pages + 1):
            body = self.search_page(address_type=address_type, page=page, per_page=per_page)
            yield from handle_page(body)

        if collected != total:
            raise BoligsidenTruncatedResultsError(
                f"collected {collected} cases across {num_pages} pages but API declared "
                f"totalHits={total} — silent truncation, do not trust this result set"
            )


def _in_postal_ranges(zip_code: int, postal_ranges: list[tuple[int, int]]) -> bool:
    return any(zip_from <= zip_code <= zip_to for zip_from, zip_to in postal_ranges)


def normalize_case(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a raw Boligsiden case dict to the fields the scorer needs.
    Confirmed field names live 2026-09-15 (see module docstring) — no
    multi-candidate guessing needed, unlike Boliga's ``normalize_listing``."""
    address = raw["address"]
    case_id = raw["caseID"]
    street = address.get("roadName", "")
    house_number = address.get("houseNumber")
    if house_number:
        street = f"{street} {house_number}".strip()
    return {
        "id": str(case_id),
        "lat": float(raw["coordinates"]["lat"]),
        "lon": float(raw["coordinates"]["lon"]),
        "price": raw.get("priceCash"),
        "size_m2": raw.get("housingArea"),
        "lot_size_m2": raw.get("lotArea"),
        "rooms": raw.get("numberOfRooms"),
        "build_year": raw.get("yearBuilt"),
        "energy_class": raw.get("energyLabel"),
        "days_on_market": raw.get("daysOnMarket"),
        "address": street or None,
        "zip_code": address.get("zipCode"),
        "town": address.get("cityName"),
        "url": f"https://boligsiden.dk/viderestilling/{case_id}",
        "boligsiden_address_slug": address.get("slug"),
    }
