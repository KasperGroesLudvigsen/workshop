"""``AddressValidator`` backed by Klimadatastyrelsen's Adressevaelger API.

This is the concrete replacement for DAWA that ``address_regex`` was left
waiting for. DAWA (``api.dataforsyningen.dk/adresser``) still answers today
but sends ``sunset``/``deprecation`` headers and **closes permanently on
2026-10-01 10:00**; Klimadatastyrelsen names Adressevaelger (search) and
Adressevask (address cleaning) as its successors. Adressevaelger is the one
that is live and reachable today, so it is what this module targets.

Two calls per validation, matching the API the official JS component uses:

  1. ``GET /adresser/soeg?tekst=<free text>&token=<token>`` -> ``{"status",
     "beskrivelse", "fund": [{"type", "id", "titel", ...}]}``
  2. ``GET /adresser/<id>?token=<token>`` -> the full record, including
     ``husnummer.adgangspunkt.koordinater`` in **EPSG:25832** — the same CRS
     ``geo/projection`` already uses, so the conversion back to WGS84 is the
     existing transformer, not a new dependency.

The search is deliberately *fuzzy* (it replaced DAWA's phonetic search), and
that is the whole reason :meth:`AdressevaelgerValidator.validate` re-checks
the hit instead of trusting it. Searching "Rentemestervej 8" returns both
"Rentemestervej 8, 2400 Koebenhavn NV" and "Rentemestervej 8, 3400
Hilleroed" — accepting the first hit would silently geocode a business into
the wrong end of the country. So a hit is only accepted when its postal code
and house number match what was asked for. That check is the register gate
the resolution pipeline is built around; without it this class would be a
fuzzy geocoder wearing a validator's name.

A token is required. ``adressevaelger123`` is the demo token published in
Klimadatastyrelsen's own README and is fine for development, but production
use wants a real one (see ``confluence.sdfi.dk/display/ADV/Brugerstyring``).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

import requests

from screener.geo.projection import _to_wgs84
from screener.resolve.address_regex import AddressCandidate, ValidatedAddress

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://adressevaelger.dk"

#: Demo token from Klimadatastyrelsen's published README. Development only.
DEMO_TOKEN = "adressevaelger123"

#: Same shape as boliga's Transport seam: (url, params) -> parsed JSON, so
#: tests drive this class without touching the network.
Transport = Callable[[str, dict[str, Any]], Any]

_HOUSE_NUMBER_RE = re.compile(r"(\d{1,3}[A-Za-z]?)\s*$")


class AdressevaelgerError(RuntimeError):
    """The service answered, but with an error or an unusable shape. Raised
    rather than returned as "no match" so a broken token or a changed
    response shape can't quietly degrade into "no business resolved
    anywhere", which looks identical to a genuinely empty region."""


def _requests_transport(url: str, params: dict[str, Any]) -> Any:
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _house_number(street: str) -> str | None:
    """Trailing house number of a street line, normalised for comparison.

    "Hovedgaden 12B" -> "12b". Returns None when the line carries no house
    number at all, which makes it unverifiable and therefore unacceptable.
    """
    match = _HOUSE_NUMBER_RE.search(street.strip())
    return match.group(1).lower() if match else None


def _postal_from_title(titel: str) -> str | None:
    """Postal code out of an ``adressebetegnelse`` like "Vej 8, 4180 Soroe"."""
    match = re.search(r",\s*(\d{4})\s+", titel)
    return match.group(1) if match else None


class AdressevaelgerValidator:
    """Validates address candidates against Denmark's live address register.

    Implements the ``AddressValidator`` protocol from ``address_regex``.
    """

    def __init__(
        self,
        token: str = DEMO_TOKEN,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: Transport = _requests_transport,
        requests_per_second: float = 5.0,
        max_hits: int = 10,
    ):
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._max_hits = max_hits
        self._last_call: float | None = None

    # -- transport -------------------------------------------------------

    def _wait(self) -> None:
        if self._min_interval and self._last_call is not None:
            remaining = self._min_interval - (time.monotonic() - self._last_call)
            if remaining > 0:
                time.sleep(remaining)
        self._last_call = time.monotonic()

    def _call(self, path: str, params: dict[str, Any]) -> Any:
        self._wait()
        body = self._transport(f"{self._base_url}/{path}", {**params, "token": self._token})
        if isinstance(body, dict) and body.get("status") == "fejl":
            raise AdressevaelgerError(f"adressevaelger error for {path}: {body.get('beskrivelse')!r}")
        return body

    def search(self, text: str) -> list[dict[str, Any]]:
        body = self._call("adresser/soeg", {"tekst": text, "maksimum": self._max_hits})
        if not isinstance(body, dict) or "fund" not in body:
            raise AdressevaelgerError(f"unexpected search response shape: {type(body).__name__}")
        return [hit for hit in body["fund"] if isinstance(hit, dict)]

    def _fetch_record(self, hit: dict[str, Any]) -> dict[str, Any] | None:
        """Full record for a search hit.

        Search returns three kinds of hit: ``adresse`` (a specific unit),
        ``husnummer`` (a street door — what we want for a business), and
        street-level types like ``vejnavn`` that carry no coordinates at
        all. Only the first two are addressable, and each lives under its
        own endpoint and its own top-level key.
        """
        hit_type = hit.get("type")
        endpoint, key = {"adresse": ("adresser", "adresse"), "husnummer": ("husnumre", "husnummer")}.get(
            hit_type, (None, None)
        )
        if endpoint is None or not hit.get("id"):
            return None
        body = self._call(f"{endpoint}/{hit['id']}", {})
        record = body.get(key) if isinstance(body, dict) else None
        return record if isinstance(record, dict) else None

    # -- validation ------------------------------------------------------

    @staticmethod
    def _queries(candidate: AddressCandidate) -> list[str]:
        """The two free-text forms to try, in order, and why there are two.

        The service's own ``postnummer`` parameter is no help — it is
        accepted and then ignored (searching "Rentemestervej 8" with
        ``postnummer`` 2400 and 3400 returns byte-identical hits) — so the
        postal code can only be steered through the free text, and the two
        ways of doing that fail in *complementary* ways:

        - **With the postal code** ("Soevej 1, 4900 Nakskov"): precise when
          it hits, but can return zero results outright for an address that
          does exist under the other form.
        - **Without it** ("Storgade 15A Soroe"): broader recall, but the
          town is not actually used for ranking, so a common street name
          ("Soevej 1" — one in nearly every town) crowds the wanted postal
          code out past the result cap entirely.

        Measured against 24 real addresses drawn from the address register
        across the target postal codes, the with-code form alone missed
        several and the without-code form alone missed one; trying both
        matched all 24. Both are gated identically afterwards, so the extra
        query costs a request, never precision.
        """
        street, town = candidate.street.strip(), candidate.town.strip()
        return [f"{street}, {candidate.postal_code} {town}".strip(" ,"), f"{street} {town}".strip()]

    def validate(self, candidate: AddressCandidate) -> ValidatedAddress | None:
        expected_number = _house_number(candidate.street)
        if expected_number is None:
            logger.info("address candidate %r has no house number — unverifiable, rejecting", candidate.street)
            return None

        for query in self._queries(candidate):
            for hit in self.search(query):
                titel = hit.get("titel") or ""
                # Cheap pre-check against the search hit's own label, so an
                # obviously-wrong town never costs a second HTTP call.
                if _postal_from_title(titel) != candidate.postal_code:
                    continue

                record = self._fetch_record(hit)
                if record is None:
                    continue
                validated = self._to_validated(record, candidate, expected_number)
                if validated is not None:
                    return validated

        logger.info(
            "no register match for %r (%s %s)", candidate.street, candidate.postal_code, candidate.town
        )
        return None

    def _to_validated(
        self, record: dict[str, Any], candidate: AddressCandidate, expected_number: str
    ) -> ValidatedAddress | None:
        # An `adresse` record nests the addressable part under `husnummer`;
        # a `husnummer` record *is* that part.
        husnummer = record.get("husnummer") if "husnummer" in record else record
        if not isinstance(husnummer, dict):
            return None

        postnummer = husnummer.get("postnummer") or {}
        postal_code = str(postnummer.get("postnr") or "")
        number = str(husnummer.get("husnummertekst") or "").lower()
        street_name = husnummer.get("vejnavn") or ""

        # The register gate: the fuzzy search proposed this, the register
        # has to confirm it's the same door we asked about.
        if postal_code != candidate.postal_code or number != expected_number:
            return None

        coords = ((husnummer.get("adgangspunkt") or {}).get("koordinater")) or {}
        x, y = coords.get("x"), coords.get("y")
        if x is None or y is None:
            logger.info("register hit %r has no adgangspunkt coordinates — rejecting", street_name)
            return None

        lon, lat = _to_wgs84().transform(float(x), float(y))
        return ValidatedAddress(
            street=f"{street_name} {husnummer.get('husnummertekst')}".strip(),
            postal_code=postal_code,
            town=str(postnummer.get("navn") or candidate.town),
            lat=lat,
            lon=lon,
        )


def validator_from_settings(settings) -> AdressevaelgerValidator:
    """Build the validator from ``config/thresholds.yaml``.

    The one place callers should construct this, so the token and rate limit
    stay config-driven rather than sprinkled through call sites.
    """
    cfg = settings.adressevaelger
    return AdressevaelgerValidator(
        token=cfg.token,
        base_url=cfg.base_url,
        requests_per_second=cfg.requests_per_second,
        max_hits=cfg.max_hits,
    )
