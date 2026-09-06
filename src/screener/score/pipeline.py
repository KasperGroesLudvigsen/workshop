"""Scoring: raw listings + prebuilt geometry index -> scored records.

Reads only from persisted raw data (normalized Boliga listings), the
prebuilt :class:`~screener.geo.store.GeometryStore`, and a resolved
business directory — never re-fetches. Re-running this with a changed
``config/thresholds.yaml`` reproduces a new result set from the same
inputs, which is the point: threshold changes are free, scraping is not.

Water (sea + eligible-lake) and the OSM-backed informational categories
(marina, playground, pool, beach) always come from the geometry store.
CVR-backed categories (hangout, grocery, fish_shop, wine_shop, ice_cream,
butcher) come from ``business_directory`` — a category -> Layer mapping
built by ``geo.business_directory.build_business_directory`` from
``resolve.pipeline``-resolved addresses. A category missing from the
directory is reported as ``None`` (not yet resolved) rather than treated as
a failure, so partial rollout of business categories doesn't break the
site's schema or wrongly fail listings.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

from shapely.geometry import Point

from screener.config import Settings
from screener.geo.amenities import category_summary
from screener.geo.projection import point_to_xy
from screener.geo.store import GeometryStore, Layer
from screener.geo.water import BadevandLookup, no_badevand_data, compute_water
from screener.score.filters import overall_passed, passes_category, passes_water_loose, passes_water_strict
from screener.site.maplinks import build_map_links

CVR_CATEGORIES = ("hangout", "grocery", "fish_shop", "wine_shop", "ice_cream", "butcher")
HARD_FILTER_CVR_CATEGORIES = ("hangout", "grocery")


def score_listing(
    listing: dict[str, Any],
    store: GeometryStore,
    settings: Settings,
    *,
    badevand_lookup: BadevandLookup = no_badevand_data,
    business_directory: dict[str, Layer] | None = None,
) -> dict[str, Any]:
    business_directory = business_directory or {}
    lon, lat = listing["lon"], listing["lat"]
    point = Point(*point_to_xy(lon, lat))
    radius_m = settings.amenity_search_radius_km * 1000.0

    water = compute_water(
        store, lon, lat, min_lake_area_ha=settings.min_lake_area_ha, badevand_lookup=badevand_lookup
    )
    water_strict_ok = passes_water_strict(water, settings.water_km)
    water_loose_ok = passes_water_loose(water, settings.water_km)

    osm_categories = {
        "marina": category_summary(store.marina, point, radius_m),
        "playground": category_summary(store.playground, point, radius_m),
        "pool": category_summary(store.pool, point, radius_m),
        "beach": category_summary(store.beach, point, radius_m),
    }
    cvr_categories = {
        name: category_summary(business_directory[name], point, radius_m) if name in business_directory else None
        for name in CVR_CATEGORIES
    }

    hangout_km = cvr_categories["hangout"]["nearest_km"] if cvr_categories["hangout"] else None
    grocery_km = cvr_categories["grocery"]["nearest_km"] if cvr_categories["grocery"] else None
    hangout_ok = passes_category(hangout_km, settings.hangout_km)
    grocery_ok = passes_category(grocery_km, settings.grocery_km)

    passed = overall_passed(
        water_strict=water_strict_ok, water_loose=water_loose_ok, hangout=hangout_ok, grocery=grocery_ok,
    )
    hard_filters_applied = ["water"] + [
        name for name in HARD_FILTER_CVR_CATEGORIES if name in business_directory
    ]

    return {
        "listing_id": listing["id"],
        "lat": lat,
        "lon": lon,
        "price": listing.get("price"),
        "size_m2": listing.get("size_m2"),
        "lot_size_m2": listing.get("lot_size_m2"),
        "rooms": listing.get("rooms"),
        "build_year": listing.get("build_year"),
        "energy_class": listing.get("energy_class"),
        "days_on_market": listing.get("days_on_market"),
        "address": listing.get("address"),
        "zip_code": listing.get("zip_code"),
        "boliga_url": listing.get("url"),
        "water": {
            "sea_distance_km": water.sea_distance_km,
            "nearest_lake_any": asdict(water.nearest_lake_any) if water.nearest_lake_any else None,
            "nearest_eligible_lake": asdict(water.nearest_eligible_lake) if water.nearest_eligible_lake else None,
            "open_water_km_strict": water.open_water_km_strict,
            "open_water_km_loose": water.open_water_km_loose,
            "passes_strict": water_strict_ok,
            "passes_loose": water_loose_ok,
        },
        **osm_categories,
        **cvr_categories,
        "passed": passed,
        "hard_filters_applied": hard_filters_applied,
        "map_links": build_map_links(lat, lon, address=listing.get("address")),
    }


def score_listings(
    listings: Iterable[dict[str, Any]],
    store: GeometryStore,
    settings: Settings,
    *,
    badevand_lookup: BadevandLookup = no_badevand_data,
    business_directory: dict[str, Layer] | None = None,
) -> list[dict[str, Any]]:
    return [
        score_listing(listing, store, settings, badevand_lookup=badevand_lookup, business_directory=business_directory)
        for listing in listings
    ]


def sort_key(record: dict[str, Any]) -> float:
    """Default sort: ascending by hangout distance. Falls back to
    open-water distance when hangout data isn't resolved yet, so output is
    still meaningfully ordered before M5's business directory is wired in
    for a given run."""
    hangout = record.get("hangout")
    if hangout and hangout.get("nearest_km") is not None:
        return hangout["nearest_km"]
    return record["water"]["open_water_km_loose"]


def sort_scored_listings(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=sort_key)
