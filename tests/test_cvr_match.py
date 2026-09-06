from __future__ import annotations

from screener.fetch.cvr import CvrClient, TransportResponse
from screener.resolve.cvr_match import match_name_to_cvr


def _client(body, status=200):
    def transport(url, params, headers):
        return TransportResponse(status, body, "{}")

    return CvrClient(transport=transport)


def test_matches_trading_name_against_legal_name():
    body = {
        "vat": "1", "name": "Bisserup Havnekro ApS", "names": ["Bisserup Havnekro"],
        "address": "Havnevej 1", "zipcode": 4243, "city": "Rude", "enddate": None,
    }
    client = _client(body)
    match = match_name_to_cvr(client, "Bisserup Havnekro", postal_code=4243)
    assert match is not None
    assert match.company.cvr_number == "1"
    assert match.score >= 0.72


def test_rejects_closed_business():
    body = {
        "vat": "1", "name": "Old Kro", "address": "X", "zipcode": 4200, "city": "Y",
        "enddate": "2020-01-01",
    }
    client = _client(body)
    assert match_name_to_cvr(client, "Old Kro", postal_code=4200) is None


def test_rejects_weak_name_match():
    body = {
        "vat": "1", "name": "Totally Unrelated Business", "address": "X", "zipcode": 4200,
        "city": "Y", "enddate": None,
    }
    client = _client(body)
    assert match_name_to_cvr(client, "Bisserup Havnekro", postal_code=4200) is None
