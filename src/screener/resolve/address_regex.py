"""Step 3 of the address-resolution pipeline: regex candidates from raw
text (a site footer, a /kontakt page), each validated against the Danish
address register before being trusted.

Danish addresses are regular: street name, house number (optionally with a
letter suffix, e.g. "12B"), a 4-digit postal code, and a town name. That
regularity is what makes this step useful even before validation — most
non-address text simply won't match the pattern.

Validation lives behind the ``AddressValidator`` protocol so the register
client is a swappable dependency. The real implementation is
``resolve.adressevaelger.AdressevaelgerValidator``, built against
Klimadatastyrelsen's Adressevaelger — DAWA's confirmed replacement, since
DAWA itself closes permanently 2026-10-01 10:00. ``NotConfiguredValidator``
below remains the default so that forgetting to pass a real validator fails
loudly instead of silently accepting unvalidated addresses.

The gate this step exists to enforce: a regex match is a *candidate*, never
a result. Only a validated hit is returned. Never geocode raw text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

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
            "no AddressValidator configured — pass "
            "resolve.adressevaelger.validator_from_settings(load_settings()) "
            "before trusting any address candidate"
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
