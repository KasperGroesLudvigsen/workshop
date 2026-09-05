"""Scoring: raw listings + prebuilt geometry index -> scored records.

Reads only from persisted raw data (normalized Boliga listings) and the
prebuilt :class:`~screener.geo.store.GeometryStore` — never re-fetches.
Re-running this with a changed ``config/thresholds.yaml`` reproduces a new
result set from the same inputs, which is the point: threshold changes are
free, scraping is not.

M3 status: water (sea + eligible-lake) and the OSM-backed informational
categories (marina, playground, pool, beach) are computed for real.
Hangout/grocery/fish_shop/wine_shop/ice_cream/butcher need CVR business
resolution (M5) and are placeholders (``None``) with the same shape they'll
have once populated, so the site template doesn't need to change later.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

from shapely.geometry import Point

from screener.config import Settings
from screener.geo.amenities import category_summary
from screener.geo.projection import point_to_xy
from screener.geo.store import GeometryStore
from screener.geo.water import BadevandLookup, no_badevand_data, compute_water
from screener.score.filters import overall_passed, passes_category, passes_water_loose, passes_water_strict
from screener.site.maplinks import build_map_links

# Categories resolved from CVR in M5+; present now with a null shape so the
# record schema (and the site template built against it) doesn't change later.
_PENDING_CVR_CATEGORIES = ("hangout", "grocery", "fish_shop", "wine_shop", "ice_cream", "butcher")


def score_listing(
    listing: dict[str, Any],
    store: GeometryStore,
    settings: Settings,
    *,
    badevand_lookup: BadevandLookup = no_badevand_data,
) -> dict[str, Any]:
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
    pending_categories = {name: None for name in _PENDING_CVR_CATEGORIES}

    passed = overall_passed(
        water_strict=water_strict_ok,
        water_loose=water_loose_ok,
        hangout=passes_category(None, settings.hangout_km),  # None until M5
        grocery=passes_category(None, settings.grocery_km),
    )

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
        **pending_categories,
        "passed": passed,
        "hard_filters_applied": ["water"],
        "map_links": build_map_links(lat, lon, address=listing.get("address")),
    }


def score_listings(
    listings: Iterable[dict[str, Any]],
    store: GeometryStore,
    settings: Settings,
    *,
    badevand_lookup: BadevandLookup = no_badevand_data,
) -> list[dict[str, Any]]:
    return [score_listing(listing, store, settings, badevand_lookup=badevand_lookup) for listing in listings]


def sort_key(record: dict[str, Any]) -> float:
    """Default sort: ascending by hangout distance. Until M5 populates
    hangout data, falls back to open-water distance so M3's demo output is
    still meaningfully ordered."""
    hangout = record.get("hangout")
    if hangout and hangout.get("nearest_km") is not None:
        return hangout["nearest_km"]
    return record["water"]["open_water_km_loose"]


def sort_scored_listings(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=sort_key)
