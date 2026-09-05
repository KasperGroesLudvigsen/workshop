"""Open-water distance and lake eligibility.

``open_water_km = min(sea_distance_km, eligible_lake_distance_km)``. Both
components are kept as separate columns on the output record so a house
6 km inland from the coast but 500 m from a lake reads differently from one
on the shore.

Lakes below ``min_lake_area_ha`` are excluded from consideration entirely —
farm ponds, drainage basins, and flooded pits carry the same OSM tags as
real lakes. Distance is measured to the lake polygon (shore), not its
centroid: ``Polygon.distance(point)`` is 0 for a point inside the polygon
and the distance to the boundary otherwise, which is exactly "distance to
shore".

**Lake eligibility** ("swimmable, where the data supports it") — a lake is
eligible if ANY of:
  - it has an official Miljoestyrelsen/EEA bathing-water (badevand)
    designation, via the injected ``badevand_lookup`` (M4 wires this up
    against the real dataset; defaults to "no match" here since that
    external source isn't integrated yet)
  - an OSM ``leisure=swimming_area`` polygon intersects the lake
  - a ``natural=beach`` way touches the lake's shore (within
    ``BEACH_SHORE_TOUCH_M`` to allow for imprecise digitisation)

Two lakes can matter for a given listing: the nearest area-qualifying lake
regardless of eligibility (``nearest_lake_any`` — always shown, so you can
see what's being excluded and why) and the nearest area-qualifying lake
that *is* eligible (``nearest_eligible_lake``, used for the strict
distance). ``lake_strict`` in config picks which one the hard filter uses
by default, but both ``open_water_km_strict`` and ``open_water_km_loose``
are always computed so a UI toggle is instant, never a re-score.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from screener.geo.projection import point_to_xy
from screener.geo.store import GeometryStore

BEACH_SHORE_TOUCH_M = 25.0
DEFAULT_LAKE_SEARCH_RADIUS_KM = 20.0

BadevandLookup = Callable[[dict], bool]


def no_badevand_data(_lake_record: dict) -> bool:
    """Default badevand lookup: always "not designated". Replaced in M4
    with a real lookup against the Miljoestyrelsen/EEA bathing-water
    dataset once that's integrated."""
    return False


@dataclass
class LakeCandidate:
    name: str | None
    osm_id: int
    distance_km: float
    area_ha: float
    eligible: bool
    eligibility_reason: str  # "badevand" | "swimming_area" | "beach_on_shore" | "none"


@dataclass
class WaterResult:
    sea_distance_km: float
    nearest_lake_any: LakeCandidate | None
    nearest_eligible_lake: LakeCandidate | None
    open_water_km_strict: float
    open_water_km_loose: float


def _lake_eligibility(
    store: GeometryStore, lake_geom: BaseGeometry, lake_record: dict, badevand_lookup: BadevandLookup
) -> tuple[bool, str]:
    if badevand_lookup(lake_record):
        return True, "badevand"
    if store.swimming_area.intersecting(lake_geom):
        return True, "swimming_area"
    if store.beach.intersecting(lake_geom, buffer_m=BEACH_SHORE_TOUCH_M):
        return True, "beach_on_shore"
    return False, "none"


def _to_candidate(record: dict, distance_m: float, eligible: bool, reason: str) -> LakeCandidate:
    return LakeCandidate(
        name=record.get("name"),
        osm_id=record["osm_id"],
        distance_km=distance_m / 1000.0,
        area_ha=record.get("area_ha", 0.0),
        eligible=eligible,
        eligibility_reason=reason,
    )


def compute_water(
    store: GeometryStore,
    lon: float,
    lat: float,
    *,
    min_lake_area_ha: float,
    search_radius_km: float = DEFAULT_LAKE_SEARCH_RADIUS_KM,
    badevand_lookup: BadevandLookup = no_badevand_data,
) -> WaterResult:
    point = Point(*point_to_xy(lon, lat))

    sea_hit = store.coastline.nearest(point)
    sea_distance_km = (sea_hit[1] / 1000.0) if sea_hit is not None else float("inf")

    candidates = store.lake.within(point, search_radius_km * 1000.0)
    qualifying = [(rec, dist, geom) for rec, dist, geom in candidates if rec.get("area_ha", 0.0) >= min_lake_area_ha]
    qualifying.sort(key=lambda triple: triple[1])

    nearest_lake_any: LakeCandidate | None = None
    nearest_eligible_lake: LakeCandidate | None = None

    if qualifying:
        first_rec, first_dist, first_geom = qualifying[0]
        first_eligible, first_reason = _lake_eligibility(store, first_geom, first_rec, badevand_lookup)
        nearest_lake_any = _to_candidate(first_rec, first_dist, first_eligible, first_reason)

        if first_eligible:
            nearest_eligible_lake = nearest_lake_any
        else:
            for rec, dist, geom in qualifying[1:]:
                eligible, reason = _lake_eligibility(store, geom, rec, badevand_lookup)
                if eligible:
                    nearest_eligible_lake = _to_candidate(rec, dist, eligible, reason)
                    break

    lake_loose_km = nearest_lake_any.distance_km if nearest_lake_any else None
    lake_strict_km = nearest_eligible_lake.distance_km if nearest_eligible_lake else None

    open_water_km_loose = min(sea_distance_km, lake_loose_km) if lake_loose_km is not None else sea_distance_km
    open_water_km_strict = min(sea_distance_km, lake_strict_km) if lake_strict_km is not None else sea_distance_km

    return WaterResult(
        sea_distance_km=sea_distance_km,
        nearest_lake_any=nearest_lake_any,
        nearest_eligible_lake=nearest_eligible_lake,
        open_water_km_strict=open_water_km_strict,
        open_water_km_loose=open_water_km_loose,
    )
