"""WGS84 <-> projected-meters conversion.

All shore-distance and area math (lake eligibility area filter, distance to
coastline/beach/lake shore) needs a projected CRS, not raw lon/lat degrees —
a degree of longitude shrinks with latitude, so lon/lat "distance" is not
comparable to a threshold in km without this. EPSG:25832 (ETRS89 / UTM zone
32N) is the standard projected CRS for Denmark and is accurate enough at
country scale for our low-single-digit-km thresholds.
"""
from __future__ import annotations

from functools import lru_cache

import shapely
from pyproj import Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

WGS84 = "EPSG:4326"
DENMARK_PROJECTED = "EPSG:25832"


@lru_cache(maxsize=1)
def _to_projected() -> Transformer:
    return Transformer.from_crs(WGS84, DENMARK_PROJECTED, always_xy=True)


@lru_cache(maxsize=1)
def _to_wgs84() -> Transformer:
    return Transformer.from_crs(DENMARK_PROJECTED, WGS84, always_xy=True)


def point_to_xy(lon: float, lat: float) -> tuple[float, float]:
    """WGS84 lon/lat -> projected (x, y) meters."""
    return _to_projected().transform(lon, lat)


def geom_to_projected(geom: BaseGeometry) -> BaseGeometry:
    """Reproject a shapely geometry from WGS84 to the Denmark projected CRS."""
    return shapely_transform(_to_projected().transform, geom)


def geom_to_wgs84(geom: BaseGeometry) -> BaseGeometry:
    return shapely_transform(_to_wgs84().transform, geom)
