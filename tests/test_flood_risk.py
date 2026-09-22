from __future__ import annotations

import json

import requests

from screener.db import Database
from screener.fetch._rate_limit import RateLimiter
from screener.fetch.flood_risk import (
    UNASSESSED,
    _parse_hazard_return_period,
    _parse_is_in_risk_area,
    fetch_flood_risk,
)


def _hazard_result(layer_name: str, pixel_value):
    return {"layerName": layer_name, "attributes": {"Classify.Pixel Value": pixel_value}}


def test_parse_hazard_return_period_picks_minimum_non_nodata():
    identify_json = {
        "results": [
            _hazard_result("Hav_Fare_10år", "NoData"),
            _hazard_result("Hav_Fare_20år", "2"),
            _hazard_result("Hav_Fare_50år", "1"),
            _hazard_result("Hav_Fare_100år", "NoData"),
        ]
    }
    assert _parse_hazard_return_period(identify_json) == 20


def test_parse_hazard_return_period_all_nodata_is_none():
    identify_json = {"results": [_hazard_result("Hav_Fare_10år", "NoData"), _hazard_result("Hav_Fare_20år", "NoData")]}
    assert _parse_hazard_return_period(identify_json) is None


def test_parse_hazard_return_period_ignores_vandlob_layers():
    identify_json = {"results": [_hazard_result("Vandløb_Fare_10år", "1")]}
    assert _parse_hazard_return_period(identify_json) is None


def test_parse_is_in_risk_area():
    assert _parse_is_in_risk_area({"results": [{"layerName": "Risikoområde Vordingborg"}]}) is True
    assert _parse_is_in_risk_area({"results": []}) is False


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict):
        self.status_code = status_code
        self._json_body = json_body
        self.text = json.dumps(json_body)

    def json(self):
        return self._json_body


def test_fetch_flood_risk_returns_unassessed_default_on_fetch_failure(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.duckdb")

    def failing_get(*args, **kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr("screener.fetch.flood_risk.requests.get", failing_get)

    result = fetch_flood_risk(11.5, 55.4, user_agent="test", db=db, rate_limiter=RateLimiter(1000.0))
    assert result == UNASSESSED
    db.close()


def test_fetch_flood_risk_caches_second_call(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.duckdb")
    call_count = {"n": 0}

    def fake_get(url, params, headers, timeout):
        call_count["n"] += 1
        if "OD_risikoomraader_2024" in url:
            return _FakeResponse(200, {"results": [{"layerName": "Risikoområde Rude"}]})
        return _FakeResponse(200, {"results": [_hazard_result("Hav_Fare_20år", "1")]})

    monkeypatch.setattr("screener.fetch.flood_risk.requests.get", fake_get)

    first = fetch_flood_risk(11.5, 55.4, user_agent="test", db=db, rate_limiter=RateLimiter(1000.0))
    assert first == {"assessed": True, "shortest_hazard_return_period_years": 20}
    calls_after_first = call_count["n"]
    assert calls_after_first == 2

    second = fetch_flood_risk(11.5, 55.4, user_agent="test", db=db, rate_limiter=RateLimiter(1000.0))
    assert second == first
    assert call_count["n"] == calls_after_first  # cache hit, no new HTTP calls
    db.close()
