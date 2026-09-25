from __future__ import annotations

import json

import pytest

from screener.db import Database
from screener.fetch._rate_limit import RateLimiter
from screener.fetch.driving_time import NO_ROUTE, fetch_driving_times, geocode_origin
from screener.resolve.address_regex import AddressCandidate, ValidatedAddress


class _FakeValidator:
    def __init__(self, accept: tuple[str, str, str], result: ValidatedAddress | None):
        self._accept = accept
        self._result = result

    def validate(self, candidate: AddressCandidate) -> ValidatedAddress | None:
        if (candidate.street, candidate.postal_code, candidate.town) == self._accept:
            return self._result
        return None


def test_geocode_origin_resolves_a_real_address():
    validated = ValidatedAddress(street="Bådehavnsgade 1", postal_code="2450", town="København SV", lat=55.6447, lon=12.5405)
    validator = _FakeValidator(("Bådehavnsgade 1", "2450", "København SV"), validated)
    assert geocode_origin("Bådehavnsgade 1, 2450 København SV", validator) == (55.6447, 12.5405)


def test_geocode_origin_raises_when_text_does_not_parse():
    validator = _FakeValidator(("", "", ""), None)
    with pytest.raises(ValueError):
        geocode_origin("not an address at all", validator)


def test_geocode_origin_raises_when_validation_fails():
    validator = _FakeValidator(("Somewhere Else", "0000", "Nowhere"), None)
    with pytest.raises(ValueError):
        geocode_origin("Bådehavnsgade 1, 2450 København SV", validator)


def _listing(listing_id: str, lat: float, lon: float) -> dict:
    return {"id": listing_id, "lat": lat, "lon": lon}


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict):
        self.status_code = status_code
        self._json_body = json_body

    def json(self):
        return self._json_body


def test_fetch_driving_times_maps_batch_results_back_to_listing_ids(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.duckdb")
    listings = [_listing("a", 55.1, 11.1), _listing("b", 55.2, 11.2)]
    call_count = {"n": 0}

    def fake_get(url, params, headers, timeout):
        call_count["n"] += 1
        return _FakeResponse(200, {"durations": [[600.0, 1200.0]], "distances": [[10000.0, 20000.0]]})

    monkeypatch.setattr("screener.fetch.driving_time.requests.get", fake_get)

    result = fetch_driving_times((55.65, 12.55), listings, db=db, rate_limiter=RateLimiter(1000.0), user_agent="test")

    assert result == {
        "a": {"duration_min": 10.0, "distance_km": 10.0},
        "b": {"duration_min": 20.0, "distance_km": 20.0},
    }
    assert call_count["n"] == 1
    db.close()


def test_fetch_driving_times_null_route_for_one_listing_does_not_affect_others(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.duckdb")
    listings = [_listing("a", 55.1, 11.1), _listing("b", 55.2, 11.2)]

    monkeypatch.setattr(
        "screener.fetch.driving_time.requests.get",
        lambda *a, **k: _FakeResponse(200, {"durations": [[None, 1200.0]], "distances": [[None, 20000.0]]}),
    )

    result = fetch_driving_times((55.65, 12.55), listings, db=db, rate_limiter=RateLimiter(1000.0), user_agent="test")

    assert result["a"] == NO_ROUTE
    assert result["b"] == {"duration_min": 20.0, "distance_km": 20.0}
    db.close()


def test_fetch_driving_times_failed_request_defaults_to_no_route(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.duckdb")
    listings = [_listing("a", 55.1, 11.1)]

    monkeypatch.setattr("screener.fetch.driving_time.requests.get", lambda *a, **k: _FakeResponse(500, {}))

    result = fetch_driving_times((55.65, 12.55), listings, db=db, rate_limiter=RateLimiter(1000.0), user_agent="test")

    assert result["a"] == NO_ROUTE
    db.close()


def test_fetch_driving_times_already_cached_listings_skip_the_network(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.duckdb")
    listings = [_listing("a", 55.1, 11.1), _listing("b", 55.2, 11.2)]
    call_count = {"n": 0}

    def fake_get(url, params, headers, timeout):
        call_count["n"] += 1
        return _FakeResponse(200, {"durations": [[600.0, 1200.0]], "distances": [[10000.0, 20000.0]]})

    monkeypatch.setattr("screener.fetch.driving_time.requests.get", fake_get)

    origin = (55.65, 12.55)
    first = fetch_driving_times(origin, listings, db=db, rate_limiter=RateLimiter(1000.0), user_agent="test")
    assert call_count["n"] == 1

    # a third, brand-new listing alongside the two already-cached ones --
    # only the new one should reach the network.
    listings_with_new = listings + [_listing("c", 55.3, 11.3)]
    second = fetch_driving_times(origin, listings_with_new, db=db, rate_limiter=RateLimiter(1000.0), user_agent="test")

    assert call_count["n"] == 2  # exactly one more batch call, for listing "c" alone
    assert second["a"] == first["a"]
    assert second["b"] == first["b"]
    assert second["c"] == {"duration_min": 10.0, "distance_km": 10.0}
    db.close()
