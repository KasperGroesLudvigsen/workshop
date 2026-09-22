from __future__ import annotations

import pytest

from screener.fetch.cvr_discovery import (
    CvrPermanentBlockedError,
    CvrPermanentClient,
    CvrPermanentResultWindowError,
    TransportResponse,
)
from screener.resolve.address_regex import AddressCandidate, ValidatedAddress
from screener.resolve.cvr_discovery import discover_and_resolve_all_categories, resolve_discovered_businesses

# -- fetch/cvr_discovery.py -----------------------------------------------


def _client(transport) -> CvrPermanentClient:
    return CvrPermanentClient(username="u", password="p", transport=transport)


def _hit(cvr_number: int, vejnavn: str = "Vej", husnr: str = "1", postnr: int = 4000) -> dict:
    return {
        "_source": {
            "Vrvirksomhed": {
                "cvrNummer": cvr_number,
                "virksomhedMetadata": {"nyesteBeliggenhedsadresse": {"vejnavn": vejnavn, "husnummerFra": husnr, "postnummer": postnr}},
            }
        }
    }


def _penhed_hit(p_number: int, vejnavn: str = "Vej", husnr: str = "1", postnr: int = 4000) -> dict:
    return {
        "_source": {
            "VrproduktionsEnhed": {
                "pNummer": p_number,
                "produktionsEnhedMetadata": {"nyesteBeliggenhedsadresse": {"vejnavn": vejnavn, "husnummerFra": husnr, "postnummer": postnr}},
            }
        }
    }


def _empty_page(url, body, auth):
    return TransportResponse(200, "{}", {"hits": {"total": 0, "hits": []}})


def test_query_shape_has_branch_should_postal_should_and_status_term_for_both_indices():
    captured = []

    def transport(url, body, auth):
        captured.append((url, body))
        return TransportResponse(200, "{}", {"hits": {"total": 0, "hits": []}})

    client = _client(transport)
    list(client.discover_active_businesses(branch_code_prefixes=["56", "47"], postal_ranges=[(4000, 4990)]))

    assert len(captured) == 2
    (virksomhed_url, virksomhed_body), (penhed_url, penhed_body) = captured
    assert "/virksomhed/_search" in virksomhed_url
    assert "/produktionsenhed/_search" in penhed_url

    for metadata_path, body in [
        ("Vrvirksomhed.virksomhedMetadata", virksomhed_body),
        ("VrproduktionsEnhed.produktionsEnhedMetadata", penhed_body),
    ]:
        branch_clause, postal_clause, status_clause = body["query"]["bool"]["must"]
        branch_prefixes = {c["prefix"][f"{metadata_path}.nyesteHovedbranche.branchekode"] for c in branch_clause["bool"]["should"]}
        assert branch_prefixes == {"56", "47"}
        postal_range = postal_clause["bool"]["should"][0]["range"][f"{metadata_path}.nyesteBeliggenhedsadresse.postnummer"]
        assert postal_range == {"gte": 4000, "lte": 4990}
        assert status_clause == {"term": {f"{metadata_path}.sammensatStatus": "aktiv"}}


def test_pages_through_all_virksomhed_results():
    pages = [
        {"hits": {"total": 3, "hits": [_hit(1, "Vej A", "1"), _hit(2, "Vej B", "2")]}},
        {"hits": {"total": 3, "hits": [_hit(3, "Vej C", "3")]}},
    ]

    def transport(url, body, auth):
        if "produktionsenhed" in url:
            return _empty_page(url, body, auth)
        return TransportResponse(200, "{}", pages[body["from"] // 200])

    client = _client(transport)
    result = list(client.discover_active_businesses(branch_code_prefixes=["56"], postal_ranges=[(4000, 4990)]))
    assert [r["cvrNummer"] for r in result] == [1, 2, 3]


def test_merges_produktionsenhed_hits_normalized_to_virksomhed_shape():
    def transport(url, body, auth):
        if "produktionsenhed" in url:
            return TransportResponse(200, "{}", {"hits": {"total": 1, "hits": [_penhed_hit(99, "Bisserup Byvej", "3", 4243)]}})
        return _empty_page(url, body, auth)

    client = _client(transport)
    result = list(client.discover_active_businesses(branch_code_prefixes=["47"], postal_ranges=[(4243, 4243)]))
    assert len(result) == 1
    assert result[0]["pNummer"] == 99
    # normalized so downstream resolve code sees virksomhedMetadata regardless of source index
    assert result[0]["virksomhedMetadata"]["nyesteBeliggenhedsadresse"]["vejnavn"] == "Bisserup Byvej"


def test_dedupes_same_physical_address_across_both_indices():
    # a small single-location company's own P-unit is often registered at
    # the identical address as its virksomhed entry -- querying both
    # indices must not double-count it.
    def transport(url, body, auth):
        if "produktionsenhed" in url:
            return TransportResponse(200, "{}", {"hits": {"total": 1, "hits": [_penhed_hit(99, "Havnevej", "1", 4243)]}})
        return TransportResponse(200, "{}", {"hits": {"total": 1, "hits": [_hit(1, "Havnevej", "1", 4243)]}})

    client = _client(transport)
    result = list(client.discover_active_businesses(branch_code_prefixes=["56"], postal_ranges=[(4243, 4243)]))
    assert len(result) == 1


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


def test_skips_hit_whose_name_matches_keyword_denylist_even_if_address_validates():
    # confirmed live: "FRIIS BYG & HAVE" (a building/garden supplies
    # business) is registered under a restaurant-adjacent CVR branch code
    # and would otherwise pass the address gate cleanly.
    hit = _raw_business("Friis Byg & Have", "Rødkullevej", "60", 4230, "Skælskør")
    validator = _FakeValidator(("Rødkullevej 60", "4230", "Skælskør"))
    result = resolve_discovered_businesses([hit], validator, category="hangout", keyword_denylist=["byg", "tømrer"])
    assert result == []


# -- discover_and_resolve_all_categories (end-to-end fetch + resolve) -----


def test_discover_and_resolve_all_categories_combines_per_category_results():
    hangout_hit = {"_source": {"Vrvirksomhed": _raw_business("Bisserup Havnekro ApS", "Havnevej", "1", 4243, "Rude")}}

    def transport(url, body, auth):
        if "/virksomhed/_search" in url and "56" in str(body):
            return TransportResponse(200, "{}", {"hits": {"total": 1, "hits": [hangout_hit]}})
        return _empty_page(url, body, auth)

    client = _client(transport)
    validator = _FakeValidator(("Havnevej 1", "4243", "Rude"))

    result = discover_and_resolve_all_categories(
        client, validator,
        branch_codes_by_category={"hangout": ["56"], "grocery": ["47"]},
        postal_ranges=[(4000, 4990)],
    )

    assert set(result.keys()) == {"hangout", "grocery"}
    assert result["grocery"] == []
    assert len(result["hangout"]) == 1
    assert result["hangout"][0].name == "Bisserup Havnekro ApS"
    assert result["hangout"][0].source_step == "cvr_discovery"
