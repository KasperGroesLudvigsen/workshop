from __future__ import annotations

import pytest

from screener.fetch.cvr import (
    CvrBlockedOrRateLimitedError,
    CvrClient,
    CvrNotFoundError,
    TransportResponse,
)


def _transport(response_by_search):
    def transport(url, params, headers):
        assert "User-Agent" in headers
        key = params.get("search") or params.get("vat")
        return response_by_search[key]

    return transport


def test_search_by_name_parses_company():
    body = {
        "vat": "12345678",
        "name": "Bisserup Havnekro ApS",
        "names": ["Bisserup Havnekro"],
        "address": "Havnevej 1",
        "zipcode": 4243,
        "city": "Rude",
        "enddate": None,
    }
    transport = _transport({"Bisserup Havnekro": TransportResponse(200, body, "{}")})
    client = CvrClient(transport=transport)
    company = client.search_by_name("Bisserup Havnekro", postal_code=4243)
    assert company.cvr_number == "12345678"
    assert company.name == "Bisserup Havnekro ApS"
    assert "Bisserup Havnekro" in company.trading_names
    assert company.zip_code == "4243"
    assert company.active is True


def test_closed_business_is_not_active():
    body = {"vat": "1", "name": "Old Kro", "address": "X", "zipcode": 4200, "city": "Y", "enddate": "2020-01-01"}
    transport = _transport({"Old Kro": TransportResponse(200, body, "{}")})
    client = CvrClient(transport=transport)
    assert client.search_by_name("Old Kro").active is False


def test_not_found_raises():
    transport = _transport({"Nonexistent": TransportResponse(404, {"error": "not found"}, "{}")})
    client = CvrClient(transport=transport)
    with pytest.raises(CvrNotFoundError):
        client.search_by_name("Nonexistent")


def test_rate_limited_raises_not_empty():
    transport = _transport({"Foo": TransportResponse(429, None, "rate limited")})
    client = CvrClient(transport=transport)
    with pytest.raises(CvrBlockedOrRateLimitedError):
        client.search_by_name("Foo")
