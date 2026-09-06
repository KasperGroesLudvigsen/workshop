"""Turns CVR-resolved business addresses into a :class:`~screener.geo.store.Layer`
so the same nearest/within-radius machinery used for OSM amenities
(coastline, marina, playground, ...) also serves CVR-backed categories
(hangout, grocery, fish shop, ...) — one code path for "how far to the
nearest X and what's within the threshold", regardless of where X's
location came from.
"""
from __future__ import annotations

from shapely.geometry import Point

from screener.geo.projection import point_to_xy
from screener.geo.store import Layer
from screener.resolve.pipeline import ResolvedBusiness


def build_business_layer(businesses: list[ResolvedBusiness]) -> Layer:
    geoms = [Point(*point_to_xy(b.lon, b.lat)) for b in businesses]
    records = [
        {"name": b.name, "source_step": b.source_step, "postal_code": b.postal_code}
        for b in businesses
    ]
    return Layer(geoms=geoms, records=records)


def build_business_directory(businesses_by_category: dict[str, list[ResolvedBusiness]]) -> dict[str, Layer]:
    return {category: build_business_layer(items) for category, items in businesses_by_category.items()}
