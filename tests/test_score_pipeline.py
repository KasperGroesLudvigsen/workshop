from __future__ import annotations

from pathlib import Path

from screener.config import load_settings
from screener.fetch.osm_extract import build_geometry_store
from screener.score.pipeline import score_listings, sort_scored_listings

FIXTURE = Path(__file__).parent / "fixtures" / "sample.osm.xml"


def _settings():
    return load_settings()


def test_listing_near_eligible_lake_passes_water_filter():
    store = build_geometry_store(FIXTURE)
    listings = [{"id": "1", "lat": 55.435188, "lon": 11.559821}]  # inside the big lake
    scored = score_listings(listings, store, _settings())
    rec = scored[0]
    assert rec["water"]["passes_strict"] is True
    assert rec["water"]["passes_loose"] is True
    assert rec["passed"] is True
    assert rec["water"]["nearest_eligible_lake"]["name"] == "Store Testso"


def test_listing_far_from_everything_fails_water_filter():
    store = build_geometry_store(FIXTURE)
    listings = [{"id": "2", "lat": 55.774067, "lon": 12.210372}]  # ~40km from anchor
    scored = score_listings(listings, store, _settings())
    rec = scored[0]
    assert rec["water"]["passes_strict"] is False
    assert rec["water"]["passes_loose"] is False
    assert rec["passed"] is False


def test_osm_backed_categories_present_and_cvr_categories_pending():
    store = build_geometry_store(FIXTURE)
    listings = [{"id": "3", "lat": 55.429518, "lon": 11.551550}]  # at the marina
    scored = score_listings(listings, store, _settings())
    rec = scored[0]
    assert rec["marina"]["nearest_km"] < 0.01
    assert any(c["name"] == "Testhavn Marina" for c in rec["marina"]["candidates"])
    for category in ("hangout", "grocery", "fish_shop", "wine_shop", "ice_cream", "butcher"):
        assert rec[category] is None


def test_map_links_use_percent_encoded_comma():
    store = build_geometry_store(FIXTURE)
    listings = [{"id": "4", "lat": 55.43, "lon": 11.55}]
    scored = score_listings(listings, store, _settings())
    links = scored[0]["map_links"]
    assert links["google_pin"] == "https://www.google.com/maps/search/?api=1&query=55.43%2C11.55"
    assert "map_action=map" in links["google_aerial"] and "basemap=satellite" in links["google_aerial"]
    assert "map_action=pano" in links["google_street"]
    assert links["apple"].startswith("https://maps.apple.com/?ll=55.43%2C11.55")


def test_sort_scored_listings_defaults_to_water_distance_until_hangout_exists():
    store = build_geometry_store(FIXTURE)
    listings = [
        {"id": "near", "lat": 55.435188, "lon": 11.559821},  # inside lake, ~0 distance
        {"id": "far", "lat": 55.774067, "lon": 12.210372},   # far from all water
    ]
    scored = score_listings(listings, store, _settings())
    ordered = sort_scored_listings(scored)
    assert [r["listing_id"] for r in ordered] == ["near", "far"]
