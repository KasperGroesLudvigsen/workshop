"""Bathing water (badevand) designations — the authoritative signal for
lake swimmability under the brief's eligibility rules.

Denmark's officially designated bathing waters (including inland lakes, not
just coast) are reported under the EU Bathing Water Directive and
aggregated by the EEA as part of its WISE bathing water dataset; Danish
specifics also live on Miljoestyrelsen's badevand.dk portal. This sandbox
cannot reach either live (see ``fetch/boliga.py``'s module docstring for the
general no-egress constraint affecting every external data source in this
build) — so **the exact current download URL and column schema below are
not verified against the live source**. This is written against the
dataset's well-documented general shape (one row per bathing-water site:
name, lat/lon, and whether it's coastal or inland), with the column names
isolated in ``_COLUMNS`` so a correction is a one-line change, and a loud
``KeyError`` if a real download doesn't match rather than silently
mis-parsing.

Matching a designated site to an OSM lake polygon is spatial, not
name-based: the official site's coordinate is often a sampling point or
jetty, not the lake's centroid, and OSM/official names for the same lake
frequently differ. A site counts as designating a lake if it falls within
``MATCH_RADIUS_M`` of the lake's polygon.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

import requests
from shapely.geometry import Point

from screener.geo.projection import point_to_xy
from screener.geo.water import BadevandLookup

logger = logging.getLogger(__name__)

# Placeholder — the brief flags this as needing verification before relying
# on it; EEA publishes per-country WISE bathing water extracts, Miljoestyrelsen
# publishes Danish-specific detail via badevand.dk. Confirm the current
# distribution format/URL from a networked machine before using this for real.
BADEVAND_SOURCE_URL = "https://www.badevand.dk/api/badevand/export"

_COLUMNS = {
    "name": "name",
    "lat": "lat",
    "lon": "lon",
    "water_type": "waterType",  # expected values include "coastal" / "inland" (or similar)
}
_INLAND_VALUES = {"inland", "indland", "sø", "soe", "lake"}

MATCH_RADIUS_M = 200.0


@dataclass
class BathingWaterSite:
    name: str
    lat: float
    lon: float
    is_inland: bool


def download_badevand_dataset(dest_path: Path | str, url: str = BADEVAND_SOURCE_URL) -> Path:
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest


def load_badevand_sites(csv_path: Path | str) -> list[BathingWaterSite]:
    sites: list[BathingWaterSite] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = set(_COLUMNS.values()) - set(reader.fieldnames or [])
        if missing:
            raise KeyError(
                f"badevand CSV missing expected columns {missing} — schema has likely "
                f"changed, do not trust a partial parse. Found columns: {reader.fieldnames}"
            )
        for row in reader:
            sites.append(
                BathingWaterSite(
                    name=row[_COLUMNS["name"]],
                    lat=float(row[_COLUMNS["lat"]]),
                    lon=float(row[_COLUMNS["lon"]]),
                    is_inland=row[_COLUMNS["water_type"]].strip().lower() in _INLAND_VALUES,
                )
            )
    inland_count = sum(1 for s in sites if s.is_inland)
    logger.info("loaded %d badevand sites (%d inland)", len(sites), inland_count)
    return sites


def build_badevand_lookup(sites: list[BathingWaterSite]) -> BadevandLookup:
    """Returns a ``BadevandLookup`` closure: given a lake's record and
    projected geometry, is any inland designated site within
    ``MATCH_RADIUS_M`` of it? Coastal sites are irrelevant here — this
    signal only feeds *lake* eligibility, sea distance doesn't need it."""
    inland_points = [Point(*point_to_xy(s.lon, s.lat)) for s in sites if s.is_inland]

    def lookup(_lake_record: dict, lake_geom) -> bool:
        return any(pt.distance(lake_geom) <= MATCH_RADIUS_M for pt in inland_points)

    return lookup
