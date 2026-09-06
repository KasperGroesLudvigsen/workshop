from __future__ import annotations

import json

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


def _transport_returning(body, status_code=200):
    def transport(url, params, headers):
        return TransportResponse(status_code=status_code, json_body=body, text=json.dumps(body))

    return transport


def test_quota_exceeded_raises_instead_of_reading_as_no_match():
    """cvrapi.dk reports a quota block as HTTP 200 with an error key — the
    body below is the real response the live service returns. Treating it as
    "no such company" would silently empty the business directory, which is
    indistinguishable from a region with no shops in it."""
    body = {
        "error": "QUOTA_EXCEEDED",
        "message": "Your quota has been exceeded. Reach out if you are certain this is a error.",
        "ip": "160.79.106.129",
    }
    client = CvrClient(transport=_transport_returning(body), requests_per_second=1000)

    with pytest.raises(CvrBlockedOrRateLimitedError, match="QUOTA_EXCEEDED"):
        client.search_by_name("Brugsen", postal_code=4180)


@pytest.mark.parametrize("code", ["BANNED", "INVALID_UA", "INTERNAL_ERROR"])
def test_other_blocking_errors_also_raise_hard(code):
    client = CvrClient(transport=_transport_returning({"error": code}), requests_per_second=1000)

    with pytest.raises(CvrBlockedOrRateLimitedError, match=code):
        client.search_by_name("Brugsen", postal_code=4180)


@pytest.mark.parametrize("code", ["NOT_FOUND", "INVALID_VAT"])
def test_genuine_misses_stay_not_found(code):
    """These two are ordinary outcomes, not failures — a small hangout
    simply may not be registered under the name we searched."""
    client = CvrClient(transport=_transport_returning({"error": code}), requests_per_second=1000)

    with pytest.raises(CvrNotFoundError):
        client.search_by_name("Ikke Et Firma", postal_code=4180)
