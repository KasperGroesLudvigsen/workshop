from __future__ import annotations

import pytest

from screener.config import BoligsidenSettings
from screener.fetch.boligsiden import (
    BoligsidenBlockedError,
    BoligsidenClient,
    BoligsidenTruncatedResultsError,
    TransportResponse,
    normalize_case,
)


def _settings() -> BoligsidenSettings:
    return BoligsidenSettings(
        base_url="https://api.boligsiden.dk/search/cases",
        requests_per_second=1000.0,  # keep tests fast
        per_page=2,
        user_agent="test-agent",
    )


def _case(case_id: str, zip_code: int) -> dict:
    return {
        "caseID": case_id,
        "coordinates": {"lat": 55.4, "lon": 11.5},
        "priceCash": 1_000_000,
        "housingArea": 60,
        "lotArea": 800,
        "numberOfRooms": 3,
        "yearBuilt": 1975,
        "daysOnMarket": 10,
        "address": {"roadName": "Havnevej", "houseNumber": "1", "zipCode": zip_code, "cityName": "Rude"},
    }


def make_transport(pages: list[list[dict]], total: int):
    def transport(url, params, headers):
        page = params["page"]
        cases = pages[page - 1] if page - 1 < len(pages) else []
        return TransportResponse(status_code=200, text="{}", json_body={"totalHits": total, "cases": cases})

    return transport


def test_fetch_postal_ranges_pages_and_filters_by_zip():
    pages = [
        [_case("a", 4243), _case("b", 9999)],  # b is outside our range
        [_case("c", 4200)],
    ]
    transport = make_transport(pages, total=3)
    client = BoligsidenClient(_settings(), transport=transport)
    result = list(
        client.fetch_postal_ranges(address_type="holiday house", postal_ranges=[(4000, 4990)], per_page=2)
    )
    assert [c["caseID"] for c in result] == ["a", "c"]


def test_fetch_postal_ranges_raises_on_truncation():
    pages = [[_case("a", 4243)]]  # only one page even though total says 3
    transport = make_transport(pages, total=3)
    client = BoligsidenClient(_settings(), transport=transport)
    with pytest.raises(BoligsidenTruncatedResultsError):
        list(client.fetch_postal_ranges(address_type="holiday house", postal_ranges=[(4000, 4990)], per_page=2))


def test_duplicate_case_id_is_skipped_not_double_counted():
    # The underlying dataset only has 1 real case; the API handing us the
    # same caseID twice across pages (confirmed live at national scale) is
    # boundary noise, not 2 real results -- totalHits reflects the former.
    dup = _case("a", 4243)
    pages = [[dup, dup]]
    transport = make_transport(pages, total=1)
    client = BoligsidenClient(_settings(), transport=transport)
    result = list(client.fetch_postal_ranges(address_type="holiday house", postal_ranges=[(4000, 4990)], per_page=2))
    assert len(result) == 1


def test_blocked_status_raises_loudly_not_empty_list():
    def transport(url, params, headers):
        return TransportResponse(status_code=403, text="blocked", json_body=None)

    client = BoligsidenClient(_settings(), transport=transport)
    with pytest.raises(BoligsidenBlockedError):
        list(client.fetch_postal_ranges(address_type="holiday house", postal_ranges=[(4000, 4990)]))


def test_normalize_case_maps_confirmed_fields():
    raw = _case("abc-123", 4243)
    listing = normalize_case(raw)
    assert listing["id"] == "abc-123"
    assert listing["lat"] == 55.4 and listing["lon"] == 11.5
    assert listing["price"] == 1_000_000
    assert listing["size_m2"] == 60
    assert listing["lot_size_m2"] == 800
    assert listing["rooms"] == 3
    assert listing["build_year"] == 1975
    assert listing["days_on_market"] == 10
    assert listing["address"] == "Havnevej 1"
    assert listing["zip_code"] == 4243
    assert listing["town"] == "Rude"
    assert listing["url"] == "https://boligsiden.dk/viderestilling/abc-123"
