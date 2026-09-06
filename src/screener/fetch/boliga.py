"""Boliga fetch client (stage 1 — raw acquisition only, no scoring logic).

``api.boliga.dk/api/v2/search/results`` is undocumented. Per the brief, its
parameter names and the fritidsbolig property-type integer code must be
*discovered*, not guessed, by watching DevTools -> Network on boliga.dk. This
sandbox's outbound network access is restricted to package registries
(PyPI/npm/GitHub) — direct requests to boliga.dk fail at the egress proxy
before ever reaching Boliga, so that discovery could not be done live from
here. Rather than bake in an unverified guess, this module:

  1. Keeps every uncertain name isolated to ``_PARAM_NAMES`` /
     ``config/thresholds.yaml`` (``boliga_property_type_fritidsbolig``,
     currently ``null``) so nothing downstream depends on a guess silently.
  2. Ships :func:`discover_property_types`, which needs no DevTools session
     at all: it fetches unfiltered results for a demographically mixed
     postal code and reads the property-type code/label pairs *Boliga's own
     response* attaches to each listing. Run it once from a machine with
     real internet access and copy the "Fritidshus" code into the config.

The parameter names in ``_PARAM_NAMES`` match the shape documented by
several public third-party Boliga-scraping write-ups (page/pageSize/sort/
zipcodeFrom/zipcodeTo/propertyType), but that is secondhand, not verified by
this session — treat them as a strong starting point to confirm, not fact.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from curl_cffi import requests as curl_requests

from screener.config import BoligaSettings
from screener.db import Database

logger = logging.getLogger(__name__)

# Isolated so a future correction is a one-line change, not a grep-and-replace.
_PARAM_NAMES = {
    "page": "page",
    "page_size": "pageSize",
    "sort": "sort",
    "zip_from": "zipcodeFrom",
    "zip_to": "zipcodeTo",
    "property_type": "propertyType",
}
_DEFAULT_PAGE_SIZE = 50
_DEFAULT_SORT = "daysForSale-a"


class BoligaBlockedError(RuntimeError):
    """Raised on HTTP 403 (or similar block signal). Never silently degrade
    a block into an empty result set — that looks identical to "no new
    houses today" and the brief calls this out explicitly."""


class BoligaTruncatedResultsError(RuntimeError):
    """Raised when a shard's actual result count doesn't match the API's
    declared total, or a single postal code alone exceeds the results cap
    and can't be sharded any finer."""


Transport = Callable[[str, dict[str, Any], dict[str, str]], "TransportResponse"]


@dataclass
class TransportResponse:
    status_code: int
    text: str
    json_body: Any


def _curl_cffi_transport(url: str, params: dict[str, Any], headers: dict[str, str]) -> TransportResponse:
    resp = curl_requests.get(url, params=params, headers=headers, impersonate="chrome124", timeout=20)
    body = None
    try:
        body = resp.json()
    except ValueError:
        pass
    return TransportResponse(status_code=resp.status_code, text=resp.text, json_body=body)


class RateLimiter:
    def __init__(self, requests_per_second: float):
        self._min_interval = 1.0 / requests_per_second
        self._last_call: float | None = None

    def wait(self) -> None:
        if self._last_call is not None:
            elapsed = time.monotonic() - self._last_call
            remaining = self._min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_call = time.monotonic()


class BoligaClient:
    def __init__(
        self,
        settings: BoligaSettings,
        db: Database | None = None,
        transport: Transport = _curl_cffi_transport,
    ):
        self._settings = settings
        self._db = db
        self._transport = transport
        self._rate_limiter = RateLimiter(settings.requests_per_second)

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._settings.user_agent,
            "Accept": "application/json",
            "Referer": "https://www.boliga.dk/",
        }

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        self._rate_limiter.wait()
        resp = self._transport(self._settings.base_url, params, self._headers())
        if self._db is not None:
            self._db.save_raw_response(
                source="boliga_search",
                url=self._settings.base_url,
                params=params,
                status_code=resp.status_code,
                body=resp.text,
            )
        if resp.status_code == 403:
            raise BoligaBlockedError(
                f"boliga returned 403 for params={params} — treat as a hard failure, "
                "not an empty result set"
            )
        if resp.status_code != 200 or resp.json_body is None:
            raise RuntimeError(f"unexpected boliga response: status={resp.status_code} body={resp.text[:500]!r}")
        return resp.json_body

    def search_page(
        self,
        *,
        zip_from: int,
        zip_to: int,
        property_type: int | None,
        page: int,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            _PARAM_NAMES["zip_from"]: zip_from,
            _PARAM_NAMES["zip_to"]: zip_to,
            _PARAM_NAMES["page"]: page,
            _PARAM_NAMES["page_size"]: page_size,
            _PARAM_NAMES["sort"]: _DEFAULT_SORT,
        }
        if property_type is not None:
            params[_PARAM_NAMES["property_type"]] = property_type
        return self._get(params)

    def fetch_shard(
        self,
        *,
        zip_from: int,
        zip_to: int,
        property_type: int | None,
        page_size: int = _DEFAULT_PAGE_SIZE,
        _depth: int = 0,
    ) -> Iterator[dict[str, Any]]:
        """Page through one postal shard, asserting the returned count
        matches the API's declared total. If the shard is too large (hits
        the results cap) it is bisected on the postal range and retried —
        recursing down to a single postal code, past which a mismatch is a
        hard error rather than something to paper over."""
        first_page = self.search_page(
            zip_from=zip_from, zip_to=zip_to, property_type=property_type, page=1, page_size=page_size
        )
        total = _extract_total_count(first_page)
        listings = _extract_listings(first_page)

        if total > self._settings.max_results_per_query:
            if zip_from == zip_to:
                raise BoligaTruncatedResultsError(
                    f"postal code {zip_from} alone has {total} results, over the "
                    f"{self._settings.max_results_per_query} cap — cannot shard further"
                )
            mid = (zip_from + zip_to) // 2
            logger.info(
                "shard %s-%s has %d results (> cap), bisecting at %d", zip_from, zip_to, total, mid
            )
            yield from self.fetch_shard(
                zip_from=zip_from, zip_to=mid, property_type=property_type,
                page_size=page_size, _depth=_depth + 1,
            )
            yield from self.fetch_shard(
                zip_from=mid + 1, zip_to=zip_to, property_type=property_type,
                page_size=page_size, _depth=_depth + 1,
            )
            return

        yield from listings
        fetched = len(listings)
        page = 2
        while fetched < total:
            body = self.search_page(
                zip_from=zip_from, zip_to=zip_to, property_type=property_type, page=page, page_size=page_size
            )
            page_listings = _extract_listings(body)
            if not page_listings:
                break
            yield from page_listings
            fetched += len(page_listings)
            page += 1

        if fetched != total:
            raise BoligaTruncatedResultsError(
                f"shard {zip_from}-{zip_to}: fetched {fetched} listings but API declared "
                f"total={total} — silent truncation, do not trust this shard's results"
            )

    def fetch_ranges(
        self, postal_ranges: list[tuple[int, int]], property_type: int | None
    ) -> Iterator[dict[str, Any]]:
        for zip_from, zip_to in postal_ranges:
            yield from self.fetch_shard(zip_from=zip_from, zip_to=zip_to, property_type=property_type)


def _extract_total_count(body: dict[str, Any]) -> int:
    """Isolated because the exact key (meta.totalCount vs totalResults vs
    meta.total...) is one of the unverified names — fix in one place."""
    meta = body.get("meta") or {}
    for key in ("totalCount", "totalResults", "total"):
        if key in meta:
            return int(meta[key])
        if key in body:
            return int(body[key])
    raise KeyError(f"could not find a total-count field in boliga response: keys={list(body.keys())}")


def _extract_listings(body: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("results", "listings", "items"):
        if key in body:
            return list(body[key])
    raise KeyError(f"could not find a listings array field in boliga response: keys={list(body.keys())}")


def _first_present(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return None


def normalize_listing(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a raw Boliga search-result dict to the fields the scorer needs.

    Field names are another of the unverified specifics (see module
    docstring) — isolated here behind multi-candidate lookups so a
    confirmed correction is a one-line change. ``id``/``lat``/``lon`` are
    required; anything else missing is left as ``None`` rather than
    raising, since those are display-only fields.
    """
    listing_id = _first_present(raw, ("id", "estateId", "listingId"))
    lat = _first_present(raw, ("latitude", "lat"))
    lon = _first_present(raw, ("longitude", "lon", "lng"))
    if listing_id is None or lat is None or lon is None:
        raise KeyError(f"boliga listing missing id/lat/lon: keys={list(raw.keys())}")
    return {
        "id": str(listing_id),
        "lat": float(lat),
        "lon": float(lon),
        "price": _first_present(raw, ("price", "priceCash")),
        "size_m2": _first_present(raw, ("size", "sizeM2", "livingArea")),
        "lot_size_m2": _first_present(raw, ("lotSize", "groundSize", "landArea")),
        "rooms": _first_present(raw, ("rooms", "numberOfRooms")),
        "build_year": _first_present(raw, ("buildYear", "yearBuilt")),
        "energy_class": _first_present(raw, ("energyClass",)),
        "days_on_market": _first_present(raw, ("daysForSale", "daysOnMarket")),
        "address": _first_present(raw, ("address", "street")),
        "zip_code": _first_present(raw, ("zipCode", "zipcode")),
        "url": _first_present(raw, ("url", "guid")),
    }


def discover_property_types(
    client: BoligaClient, *, sample_zip: int = 4200, page_size: int = 100
) -> dict[Any, set[str]]:
    """Fetch unfiltered results for one postal code and collect the
    (code -> observed label strings) Boliga's own payload attaches to each
    listing, so the fritidsbolig code can be read off without a DevTools
    session. Run from a networked machine; not runnable in this sandbox."""
    body = client.search_page(zip_from=sample_zip, zip_to=sample_zip, property_type=None, page=1, page_size=page_size)
    listings = _extract_listings(body)
    codes: dict[Any, set[str]] = {}
    for listing in listings:
        code = listing.get("propertyType")
        label = listing.get("propertyTypeName") or listing.get("propertyType_da") or listing.get("type")
        if code is None:
            continue
        codes.setdefault(code, set())
        if label:
            codes[code].add(str(label))
    return codes
