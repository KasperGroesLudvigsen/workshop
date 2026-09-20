"""Step 3 of the address-resolution pipeline: regex candidates from raw
text (a site footer, a /kontakt page), each validated against the Danish
address register before being trusted.

Danish addresses are regular: street name, house number (optionally with a
letter suffix, e.g. "12B"), a 4-digit postal code, and a town name. That
regularity is what makes this step useful even before validation — most
non-address text simply won't match the pattern.

DAWA (the old address API) shuts down 2026-10-01. Its replacement,
``DatafordelerAddressValidator`` below, is confirmed live against DAR's
GraphQL v3 endpoint as of 2026-09-15 — see that class's docstring for the
query shape. Validation is behind the ``AddressValidator`` protocol so
nothing else in this module or ``resolve/pipeline.py`` needs to change.

The gate this step exists to enforce: a regex match is a *candidate*, never
a result. Only a validated hit is returned. Never geocode raw text.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import requests
import shapely.wkt

from screener.fetch._rate_limit import RateLimiter
from screener.geo.projection import geom_to_wgs84

# Street name / number(+letter) / 4-digit postal code / town. Each word of
# the street name must itself start with a capital letter (Danish street
# names do) so this doesn't swallow an entire lowercase sentence that
# happens to end in "<Word> <number>, <postal> <Town>".
_STREET_WORD = r"[A-ZÆØÅ][a-zæøåA-ZÆØÅ.'\-]*"
_ADDRESS_RE = re.compile(
    rf"(?P<street>(?:{_STREET_WORD}\s){{0,3}}{_STREET_WORD}\s\d{{1,3}}[A-Za-z]?)"
    rf"[,\s]+(?P<postal>\d{{4}})\s+(?P<town>{_STREET_WORD}(?:\s{_STREET_WORD})?)"
)


@dataclass
class AddressCandidate:
    street: str
    postal_code: str
    town: str
    raw_text: str


@dataclass
class ValidatedAddress:
    street: str
    postal_code: str
    town: str
    lat: float
    lon: float


class AddressValidator(Protocol):
    def validate(self, candidate: AddressCandidate) -> ValidatedAddress | None:
        """Return a geocoded, register-confirmed address, or None if the
        candidate does not resolve to a real entry."""
        ...


class NotConfiguredValidator:
    """Default validator: rejects everything. Fails loudly (raises) rather
    than silently accepting unvalidated addresses if anyone forgets to wire
    in a real one, since that would defeat the whole point of this gate."""

    def validate(self, candidate: AddressCandidate) -> ValidatedAddress | None:
        raise NotImplementedError(
            "no AddressValidator configured — pass a DatafordelerAddressValidator "
            "before trusting any address_regex candidate"
        )


_DAR_GAELDENDE_STATUS = "3"  # DAR livscyklus kodeliste: 1=Intern forberedelse, 2=Foreloebig, 3=Gaeldende, 4=Nedlagt

# DAR's only text-search filter (adgangsadressebetegnelse) supports startsWith/eq/in,
# not "contains", and every list query on this schema requires either an
# id/rowId filter or a virkningstid/registreringstid argument -- confirmed live.
_HUSNUMMER_QUERY = """
query($street: String!, $vtid: DafDateTime!) {
  DAR_Husnummer(virkningstid: $vtid, where: { adgangsadressebetegnelse: { startsWith: $street } }, first: 10) {
    nodes {
      adgangsadressebetegnelse
      status
      adgangspunkt
    }
  }
}
"""

_ADRESSEPUNKT_QUERY = """
query($id: String!, $vtid: DafDateTime!) {
  DAR_Adressepunkt(virkningstid: $vtid, where: { id_lokalId: { eq: $id } }, first: 1) {
    nodes {
      position { wkt }
    }
  }
}
"""


def _parse_postal_from_betegnelse(betegnelse: str) -> str | None:
    """"Havnevej 1, 4243 Rude" -> "4243". DAR's postnummer field on
    DAR_Husnummer is an opaque reference id, not the 4-digit code, so the
    only place the plain code is available for a cross-check is this
    human-readable string DAR itself renders.

    Bug found live 2026-09-19: an address with a "supplerende bynavn" (a
    hamlet/village name DAR inserts between the street and the postal
    code -- common for small places within a larger postal town, e.g.
    "Bisserup Byvej 3, Bisserup, 4243 Rude") has *two* commas, not one.
    Taking the first comma-separated segment silently mis-parsed the
    bynavn itself ("Bisserup,") as the postal code, failed `isdigit()`,
    and dropped an otherwise perfectly valid, Gaeldende address. The
    postal code always immediately precedes the town in the *last*
    segment, regardless of how many bynavn segments precede it."""
    tail = betegnelse.rsplit(", ", 1)[-1]
    postal, _, _ = tail.strip().partition(" ")
    return postal if postal.isdigit() and len(postal) == 4 else None


class DatafordelerAddressValidator:
    """Confirmed live 2026-09-15 against DAR's (Danmarks Adresseregister)
    GraphQL v3 endpoint, DAWA's replacement. Two round trips per candidate:

    1. ``DAR_Husnummer`` filtered by ``adgangsadressebetegnelse.startsWith``
       (the candidate's "Street Number" text — DAR's only free-text filter).
       Keeps only a node whose status is "Gaeldende" (3) and whose own
       recorded postal code (parsed from the betegnelse string, since the
       schema's ``postnummer`` field is an opaque reference id, not the
       4-digit code) matches the candidate's postal code.
    2. ``DAR_Adressepunkt`` filtered by ``id_lokalId`` on that node's
       ``adgangspunkt`` reference, to get its ``position`` (WKT point in
       EPSG:25832 — reprojected to WGS84 to match ``ValidatedAddress``).

    Every list query on this schema requires either an id/rowId filter or a
    bitemporal ``virkningstid``/``registreringstid`` argument (confirmed
    live via a 400 without one) — ``virkningstid: now`` is passed on both
    calls to mean "as currently registered".

    Rate-limited (default 5 req/sec, i.e. up to 2 real HTTP calls per
    ``validate()``) — no documented DAR quota, but bulk business discovery
    (``resolve/cvr_discovery.py``) can call ``validate()`` thousands of
    times in a single run, and this was unthrottled until that usage
    pattern existed.
    """

    BASE_URL = "https://graphql.datafordeler.dk/DAR/v3"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        session: requests.Session | None = None,
        base_url: str = BASE_URL,
        requests_per_second: float = 5.0,
    ):
        api_key = api_key or os.environ.get("DATAFORDELER_DAR_API_KEY")
        if not api_key:
            raise RuntimeError(
                "DATAFORDELER_DAR_API_KEY not set — required for DatafordelerAddressValidator"
            )
        self._api_key = api_key
        self._session = session or requests.Session()
        self._base_url = base_url
        self._rate_limiter = RateLimiter(requests_per_second)

    def _query(self, query: str, variables: dict) -> dict:
        self._rate_limiter.wait()
        resp = self._session.post(
            self._base_url,
            params={"apiKey": self._api_key},
            json={"query": query, "variables": variables},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"DAR GraphQL error for variables={variables}: {body['errors']}")
        return body["data"]

    def validate(self, candidate: AddressCandidate) -> ValidatedAddress | None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        husnummer_data = self._query(_HUSNUMMER_QUERY, {"street": candidate.street, "vtid": now})

        match = None
        for node in husnummer_data["DAR_Husnummer"]["nodes"]:
            if node["status"] != _DAR_GAELDENDE_STATUS:
                continue
            if _parse_postal_from_betegnelse(node["adgangsadressebetegnelse"]) != candidate.postal_code:
                continue
            match = node
            break
        if match is None:
            return None

        point_data = self._query(_ADRESSEPUNKT_QUERY, {"id": match["adgangspunkt"], "vtid": now})
        nodes = point_data["DAR_Adressepunkt"]["nodes"]
        if not nodes or not nodes[0]["position"]:
            return None

        point_25832 = shapely.wkt.loads(nodes[0]["position"]["wkt"])
        point_wgs84 = geom_to_wgs84(point_25832)
        return ValidatedAddress(
            street=candidate.street,
            postal_code=candidate.postal_code,
            town=candidate.town,
            lat=point_wgs84.y,
            lon=point_wgs84.x,
        )


def find_address_candidates(text: str) -> list[AddressCandidate]:
    candidates = []
    for match in _ADDRESS_RE.finditer(text):
        candidates.append(
            AddressCandidate(
                street=match.group("street").strip(),
                postal_code=match.group("postal"),
                town=match.group("town").strip(),
                raw_text=match.group(0),
            )
        )
    return candidates


def resolve_first_valid(
    text: str, validator: AddressValidator, *, expected_postal_code: str | None = None
) -> ValidatedAddress | None:
    for candidate in find_address_candidates(text):
        if expected_postal_code is not None and candidate.postal_code != expected_postal_code:
            continue
        validated = validator.validate(candidate)
        if validated is not None:
            return validated
    return None
