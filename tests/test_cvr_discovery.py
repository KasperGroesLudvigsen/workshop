from __future__ import annotations

import pytest

from screener.fetch.cvr_discovery import (
    CvrPermanentBlockedError,
    CvrPermanentClient,
    CvrPermanentResultWindowError,
    TransportResponse,
)
from screener.resolve.address_regex import AddressCandidate, ValidatedAddress
from screener.resolve.cvr_discovery import resolve_discovered_businesses

# -- fetch/cvr_discovery.py -----------------------------------------------


def _client(transport) -> CvrPermanentClient:
    return CvrPermanentClient(username="u", password="p", transport=transport)


def _hit(cvr_number: int) -> dict:
    return {"_source": {"Vrvirksomhed": {"cvrNummer": cvr_number}}}


def test_query_shape_has_branch_should_postal_should_and_status_term():
    captured = {}

    def transport(url, body, auth):
        captured["body"] = body
        return TransportResponse(200, "{}", {"hits": {"total": 0, "hits": []}})

    client = _client(transport)
    list(client.discover_active_businesses(branch_code_prefixes=["56", "47"], postal_ranges=[(4000, 4990)]))

    must = captured["body"]["query"]["bool"]["must"]
    branch_clause, postal_clause, status_clause = must
    branch_prefixes = {c["prefix"]["Vrvirksomhed.virksomhedMetadata.nyesteHovedbranche.branchekode"] for c in branch_clause["bool"]["should"]}
    assert branch_prefixes == {"56", "47"}
    postal_range = postal_clause["bool"]["should"][0]["range"]["Vrvirksomhed.virksomhedMetadata.nyesteBeliggenhedsadresse.postnummer"]
    assert postal_range == {"gte": 4000, "lte": 4990}
    assert status_clause == {"term": {"Vrvirksomhed.virksomhedMetadata.sammensatStatus": "aktiv"}}


def test_pages_through_all_results():
    pages = [
        {"hits": {"total": 3, "hits": [_hit(1), _hit(2)]}},
        {"hits": {"total": 3, "hits": [_hit(3)]}},
    ]

    def transport(url, body, auth):
        return TransportResponse(200, "{}", pages[body["from"] // 200])

    client = _client(transport)
    result = list(client.discover_active_businesses(branch_code_prefixes=["56"], postal_ranges=[(4000, 4990)]))
    assert [r["cvrNummer"] for r in result] == [1, 2, 3]


def test_blocked_status_raises_loudly():
    def transport(url, body, auth):
        return TransportResponse(403, "blocked", None)

    client = _client(transport)
    with pytest.raises(CvrPermanentBlockedError):
        list(client.discover_active_businesses(branch_code_prefixes=["56"], postal_ranges=[(4000, 4990)]))


def test_raises_when_result_window_exceeded():
    def transport(url, body, auth):
        return TransportResponse(200, "{}", {"hits": {"total": 50_000, "hits": []}})

    client = _client(transport)
    with pytest.raises(CvrPermanentResultWindowError):
        list(client.discover_active_businesses(branch_code_prefixes=["56"], postal_ranges=[(4000, 4990)]))


# -- resolve/cvr_discovery.py -----------------------------------------------


def _raw_business(name: str, road: str, house_number: str, postal: int, town: str) -> dict:
    return {
        "cvrNummer": 12345678,
        "virksomhedMetadata": {
            "nyesteNavn": {"navn": name},
            "nyesteBeliggenhedsadresse": {
                "vejnavn": road, "husnummerFra": house_number, "postnummer": postal, "postdistrikt": town,
            },
        },
    }


class _FakeValidator:
    def __init__(self, accept: tuple[str, str, str]):
        self._accept = accept

    def validate(self, candidate: AddressCandidate) -> ValidatedAddress | None:
        if (candidate.street, candidate.postal_code, candidate.town) == self._accept:
            return ValidatedAddress(street=candidate.street, postal_code=candidate.postal_code, town=candidate.town, lat=55.4, lon=11.5)
        return None


def test_resolves_validated_hit():
    hit = _raw_business("Bisserup Havnekro ApS", "Havnevej", "1", 4243, "Rude")
    validator = _FakeValidator(("Havnevej 1", "4243", "Rude"))
    result = resolve_discovered_businesses([hit], validator, category="hangout")
    assert len(result) == 1
    assert result[0].name == "Bisserup Havnekro ApS"
    assert result[0].source_step == "cvr_discovery"
    assert result[0].lat == 55.4


def test_drops_hit_that_fails_validation():
    hit = _raw_business("Ghost Kro", "Nowhere Vej", "9", 9999, "Nergonby")
    validator = _FakeValidator(("Havnevej 1", "4243", "Rude"))
    assert resolve_discovered_businesses([hit], validator, category="hangout") == []


def test_skips_hit_missing_address_fields():
    hit = {"cvrNummer": 1, "virksomhedMetadata": {"nyesteNavn": {"navn": "No Address ApS"}, "nyesteBeliggenhedsadresse": {}}}
    validator = _FakeValidator(("Havnevej 1", "4243", "Rude"))
    assert resolve_discovered_businesses([hit], validator, category="hangout") == []
