"""CVR lookup client — cvrapi.dk (free, unofficial, no registration).

Per the build-order decision, this targets cvrapi.dk rather than the
official CVR API (Erhvervsstyrelsen/Datafordeleren), which needs a
registered service agreement. Trade-off worth being explicit about: cvrapi.dk
is a *lookup* API (by name or by CVR number) — it answers "does a business
matching this name exist, and where" — not a *bulk enumeration* API. It
cannot answer "list every hangout/grocery business in postal codes
3000-3699" the way the official API's industry-code + postal-code search
can. So in this pipeline, cvrapi.dk serves resolve/cvr_match.py (turning a
candidate name from OSM/web-search discovery into an authoritative,
active/ophoert-checked address) — it is not itself the discovery mechanism
the brief describes CVR as being. Swapping to the official API later only
means replacing this client; resolve/cvr_match.py's interface doesn't change.

This sandbox cannot reach cvrapi.dk (see fetch/boliga.py's docstring for the
general no-egress constraint), so the exact response schema below is from
public documentation/community usage, not verified live here. cvrapi.dk
asks callers to identify themselves with a descriptive User-Agent and to
keep request volume low — no documented hard rate limit, so this errs
conservative (same 1 req/sec default as Boliga).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from screener.db import Database

BASE_URL = "https://cvrapi.dk/api"
DEFAULT_USER_AGENT = "summer-house-screener/0.1 (contact: set a real email before deploying)"


class CvrNotFoundError(RuntimeError):
    """No CVR entry matched the query — a legitimate outcome (many local
    hangouts are sole proprietorships or too small to require different
    handling), not necessarily a pipeline error."""


class CvrBlockedOrRateLimitedError(RuntimeError):
    """cvrapi.dk returned 429/403. Treated as a hard failure so it isn't
    mistaken for "no match found"."""


Transport = Callable[[str, dict[str, Any], dict[str, str]], "TransportResponse"]


@dataclass
class TransportResponse:
    status_code: int
    json_body: Any
    text: str


def _requests_transport(url: str, params: dict[str, Any], headers: dict[str, str]) -> TransportResponse:
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    body = None
    try:
        body = resp.json()
    except ValueError:
        pass
    return TransportResponse(status_code=resp.status_code, json_body=body, text=resp.text)


class RateLimiter:
    def __init__(self, requests_per_second: float):
        self._min_interval = 1.0 / requests_per_second
        self._last_call: float | None = None

    def wait(self) -> None:
        if self._last_call is not None:
            remaining = self._min_interval - (time.monotonic() - self._last_call)
            if remaining > 0:
                time.sleep(remaining)
        self._last_call = time.monotonic()


@dataclass
class CvrCompany:
    cvr_number: str
    name: str
    trading_names: list[str]
    address: str | None
    zip_code: str | None
    city: str | None
    active: bool
    raw: dict[str, Any]


def _parse_company(body: dict[str, Any]) -> CvrCompany:
    # Field names per cvrapi.dk's documented shape; isolated for the same
    # reason as Boliga's _PARAM_NAMES — a live discrepancy is a one-line fix.
    trading_names = []
    for key in ("names", "binames", "secondaryname"):
        val = body.get(key)
        if isinstance(val, list):
            trading_names.extend(str(v) for v in val if v)
        elif isinstance(val, str) and val:
            trading_names.append(val)
    end_date = body.get("enddate")
    return CvrCompany(
        cvr_number=str(body.get("vat") or body.get("cvr") or ""),
        name=str(body.get("name") or ""),
        trading_names=trading_names,
        address=body.get("address"),
        zip_code=str(body["zipcode"]) if body.get("zipcode") is not None else None,
        city=body.get("city"),
        active=not end_date,
        raw=body,
    )


class CvrClient:
    def __init__(
        self,
        db: Database | None = None,
        transport: Transport = _requests_transport,
        requests_per_second: float = 1.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        self._db = db
        self._transport = transport
        self._rate_limiter = RateLimiter(requests_per_second)
        self._user_agent = user_agent

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        self._rate_limiter.wait()
        headers = {"User-Agent": self._user_agent}
        resp = self._transport(BASE_URL, params, headers)
        if self._db is not None:
            self._db.save_raw_response(
                source="cvr_lookup", url=BASE_URL, params=params, status_code=resp.status_code, body=resp.text
            )
        if resp.status_code in (403, 429):
            raise CvrBlockedOrRateLimitedError(f"cvrapi.dk returned {resp.status_code} for params={params}")
        if resp.status_code == 404 or (resp.json_body or {}).get("error"):
            raise CvrNotFoundError(f"no CVR match for params={params}")
        if resp.status_code != 200 or resp.json_body is None:
            raise RuntimeError(f"unexpected cvrapi.dk response: status={resp.status_code} body={resp.text[:300]!r}")
        return resp.json_body

    def search_by_name(self, name: str, *, postal_code: int | None = None) -> CvrCompany:
        params: dict[str, Any] = {"search": name, "country": "dk"}
        if postal_code is not None:
            params["zipcode"] = postal_code
        return _parse_company(self._get(params))

    def lookup_by_vat(self, cvr_number: str) -> CvrCompany:
        return _parse_company(self._get({"vat": cvr_number, "country": "dk"}))
