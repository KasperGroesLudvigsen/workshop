from __future__ import annotations

import json

import requests

from screener.db import Database
from screener.fetch._rate_limit import RateLimiter
from screener.fetch.flood_risk import NO_RISK_FOUND, _parse_scenarios, fetch_flood_risk


def _hazard(year: str, return_period: str) -> dict:
    return {"layerName": f"Oversvømmelsesfare i {year} - {return_period}-års hændelse"}


def _depth(year: str, return_period: str, pixel_value, sep: str = "-", suffix: str = "år") -> dict:
    return {
        "layerName": f"Oversvømmelsesdybde i {year} - {return_period}{sep}{suffix} hændelse",
        "attributes": {"Classify.Pixel Value": pixel_value},
    }


def test_parse_scenarios_picks_up_hazard_and_depth():
    identify_json = {"results": [_hazard("2020", "50"), _depth("2020", "50", "0.32")]}
    scenarios = _parse_scenarios(identify_json)
    assert scenarios["2020"][50] == {"hazard": True, "depth_m": 0.32}


def test_parse_scenarios_handles_inconsistent_layer_name_formatting():
    # confirmed live: depth layers mix "50-år" (hyphen, no trailing s) with
    # "100 år" / "1.000 år" (space, no hyphen) for other return periods.
    identify_json = {
        "results": [
            _depth("2020", "50", "0.1", sep="-", suffix="år"),
            _depth("2020", "100", "0.2", sep=" ", suffix="år"),
            _depth("2020", "1.000", "0.3", sep=" ", suffix="år"),
        ]
    }
    scenarios = _parse_scenarios(identify_json)
    assert scenarios["2020"][50]["depth_m"] == 0.1
    assert scenarios["2020"][100]["depth_m"] == 0.2
    assert scenarios["2020"][1000]["depth_m"] == 0.3


def test_parse_scenarios_a_real_depth_reading_counts_as_hazard_even_without_the_polygon_hit():
    # confirmed live: a tight query can make the vector hazard polygon miss
    # a point right at a flood-zone boundary while the raster depth layer
    # still returns a real value there -- the depth reading alone is
    # treated as sufficient evidence.
    identify_json = {"results": [_depth("2020", "50", "0.32")]}
    scenarios = _parse_scenarios(identify_json)
    assert scenarios["2020"][50] == {"hazard": True, "depth_m": 0.32}


def test_parse_scenarios_depth_nodata_is_no_hazard():
    identify_json = {"results": [_depth("2020", "50", "NoData")]}
    scenarios = _parse_scenarios(identify_json)
    assert scenarios["2020"][50] == {"hazard": False, "depth_m": None}


def test_parse_scenarios_empty_results():
    assert _parse_scenarios({"results": []}) == {}


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict):
        self.status_code = status_code
        self._json_body = json_body
        self.text = json.dumps(json_body)

    def json(self):
        return self._json_body


def test_fetch_flood_risk_returns_no_risk_found_default_on_fetch_failure(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.duckdb")

    def failing_get(*args, **kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr("screener.fetch.flood_risk.requests.get", failing_get)

    result = fetch_flood_risk(11.5, 55.4, user_agent="test", db=db, rate_limiter=RateLimiter(1000.0))
    assert result == NO_RISK_FOUND
    db.close()


def test_fetch_flood_risk_returns_no_risk_found_when_nothing_matches(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.duckdb")
    monkeypatch.setattr(
        "screener.fetch.flood_risk.requests.get",
        lambda *a, **k: _FakeResponse(200, {"results": []}),
    )
    result = fetch_flood_risk(11.55, 55.43, user_agent="test", db=db, rate_limiter=RateLimiter(1000.0))
    assert result == NO_RISK_FOUND
    db.close()


def test_fetch_flood_risk_summarizes_current_and_future_year(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.duckdb")
    call_count = {"n": 0}

    def fake_get(url, params, headers, timeout):
        call_count["n"] += 1
        return _FakeResponse(200, {
            "results": [
                _hazard("2020", "100"),
                _depth("2020", "100", "0.42"),
                _hazard("2120", "50"),
                _depth("2120", "50", "1.10"),
            ]
        })

    monkeypatch.setattr("screener.fetch.flood_risk.requests.get", fake_get)

    first = fetch_flood_risk(11.5, 55.4, user_agent="test", db=db, rate_limiter=RateLimiter(1000.0))
    assert first == {
        "current_return_period_years": 100,
        "current_depth_m": 0.42,
        "2120_return_period_years": 50,
        "2120_depth_m": 1.10,
    }
    calls_after_first = call_count["n"]
    assert calls_after_first == 1  # a single identify call now covers everything

    second = fetch_flood_risk(11.5, 55.4, user_agent="test", db=db, rate_limiter=RateLimiter(1000.0))
    assert second == first
    assert call_count["n"] == calls_after_first  # cache hit, no new HTTP calls
    db.close()
