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

**Quota, verified against the live service and its documentation: 50
lookups per day, counted per IP *range*.** That is a hard ceiling on how
much this client can do, and it is low enough to be an architectural
constraint rather than a tuning detail — resolving a few hundred candidate
businesses across the region is days of budget, so a real run needs either
an issued token (cvrapi.dk grants higher limits on request) or the official
Erhvervsstyrelsen API. See docs/HANDOFF.md.

Error handling is shaped by a verified quirk: cvrapi.dk reports *all* of its
error conditions as **HTTP 200** with an ``error`` key in the body, so
status code alone cannot distinguish "this business does not exist" from
"you are blocked". Conflating the two is the exact failure the Boliga client
refuses (a block that reads as an empty result is indistinguishable from a
genuinely empty region), so :data:`_HARD_FAILURE_ERRORS` splits them and
only genuine misses raise :class:`CvrNotFoundError`.

cvrapi.dk rejects generic user agents with ``INVALID_UA`` and documents the
expected form: company name, project name, and a contact. Callers must pass
a real one — :data:`DEFAULT_USER_AGENT` carries the template, not a usable
value.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from screener.db import Database

BASE_URL = "https://cvrapi.dk/api"
BASE_USER_AGENT_TEMPLATE = "summer-house-screener - {contact}"
DEFAULT_USER_AGENT = BASE_USER_AGENT_TEMPLATE.format(contact="SET A REAL CONTACT EMAIL OR PHONE")

#: cvrapi.dk error codes that mean "stop", not "no such company". All of
#: them arrive as HTTP 200, so they are only distinguishable by this string.
_HARD_FAILURE_ERRORS = {"QUOTA_EXCEEDED", "BANNED", "INVALID_UA", "INTERNAL_ERROR"}

#: Documented daily allowance per IP range, for callers that want to budget.
FREE_DAILY_LOOKUP_QUOTA = 50


class CvrNotFoundError(RuntimeError):
    """No CVR entry matched the query — a legitimate outcome (many local
    hangouts are sole proprietorships or too small to require different
    handling), not necessarily a pipeline error."""


class CvrBlockedOrRateLimitedError(RuntimeError):
    """cvrapi.dk blocked the call — HTTP 429/403, or (much more commonly) an
    HTTP 200 carrying QUOTA_EXCEEDED, BANNED, INVALID_UA or INTERNAL_ERROR.
    Treated as a hard failure so it is never mistaken for "no match found",
    which would quietly empty the business directory instead of failing."""


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
    # `vat`, `name`, `address`, `zipcode`, `city` and `enddate` are confirmed
    # against cvrapi.dk's own documentation and published response examples;
    # `vat` comes back as an int and `zipcode` as either, hence the str()
    # coercions. The trading-name keys below are the one part still
    # unconfirmed — the docs don't cover binavne — so all three plausible
    # spellings are accepted rather than betting on one.
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
        error_code = (resp.json_body or {}).get("error")
        if error_code in _HARD_FAILURE_ERRORS:
            raise CvrBlockedOrRateLimitedError(
                f"cvrapi.dk returned {error_code} (HTTP {resp.status_code}) for params={params} — "
                f"the free allowance is {FREE_DAILY_LOOKUP_QUOTA} lookups/day per IP range"
            )
        if resp.status_code == 404 or error_code:
            raise CvrNotFoundError(f"no CVR match for params={params} (error={error_code!r})")
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
