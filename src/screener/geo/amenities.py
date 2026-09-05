"""Per-category nearest-distance + within-radius candidate list.

One function serves every amenity category (hangout, grocery, marina,
playground, pool, beach, fish shop, ...): a listing's distance to the
*nearest* member of a category is one number, but "how many are within the
threshold" and "what are they called" both depend on a threshold the user
can change live in the UI. So instead of baking in a count at the default
radius, we ship every candidate within a generous outer search radius
(`amenity_search_radius_km` in config) as a sorted (name, distance_km) list;
the site recomputes count/names for whatever radius is currently set
without a server round-trip.
"""
from __future__ import annotations

from shapely.geometry import Point

from screener.geo.store import Layer


def category_summary(layer: Layer, point: Point, search_radius_m: float) -> dict:
    nearest = layer.nearest(point)
    within = layer.within(point, search_radius_m)
    return {
        "nearest_km": (nearest[1] / 1000.0) if nearest else None,
        "candidates": [
            {"name": record.get("name") or "(unnamed)", "distance_km": dist / 1000.0}
            for record, dist, _geom in within
        ],
    }
