"""Bathing water (badevand) designations — the authoritative signal for
lake swimmability under the brief's eligibility rules.

Confirmed live 2026-09-15 via Danmarks Miljøportal's Arealdata catalog
(https://arealdata.miljoeportal.dk/datasets/urn:dmp:ds:badevand-stamdata):
the real authoritative distribution is **not** badevand.dk (a consumer-facing
site with no confirmed download API) but PULS's own public GeoServer WFS
layer ``puls:Badevand`` at ``BADEVAND_WFS_URL`` — no auth needed, license
CC0 1.0. A real pull returned 1488 stations with an explicit ``WaterType``
of ``"Marin"`` (coastal), ``"Ferskvand"`` (inland/freshwater — our
eligibility signal), or ``"Ukendt"`` (unknown), plus a ``Closed`` date for
decommissioned stations. Geometry comes back already in EPSG:25832 (the
project's own working projected CRS, see ``geo/projection.py``), so no
WGS84 round-trip is needed.

Matching a designated site to an OSM lake polygon is spatial, not
name-based: the official site's coordinate is often a sampling point or
jetty, not the lake's centroid, and OSM/official names for the same lake
frequently differ. A site counts as designating a lake if it falls within
``MATCH_RADIUS_M`` of the lake's polygon.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import requests
from shapely.geometry import Point

from screener.geo.water import BadevandLookup

logger = logging.getLogger(__name__)

BADEVAND_WFS_URL = "https://pulsgeo.miljoeportal.dk/geoserver/wfs"
BADEVAND_WFS_TYPENAME = "puls:Badevand"

_PROPERTIES = {
    "name": "Name",
    "water_type": "WaterType",
    "closed": "Closed",
}
_INLAND_VALUES = {"ferskvand"}

MATCH_RADIUS_M = 200.0


@dataclass
class BathingWaterSite:
    name: str
    x: float  # EPSG:25832 easting, metres
    y: float  # EPSG:25832 northing, metres
    is_inland: bool


def download_badevand_dataset(
    dest_path: Path | str, url: str = BADEVAND_WFS_URL, typename: str = BADEVAND_WFS_TYPENAME
) -> Path:
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(
        url,
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": typename,
            "outputFormat": "application/json",
            "srsName": "urn:ogc:def:crs:EPSG::25832",
        },
        timeout=60,
    )
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest


def load_badevand_sites(geojson_path: Path | str) -> list[BathingWaterSite]:
    with open(geojson_path, encoding="utf-8") as f:
        data = json.load(f)
    features = data.get("features")
    if features is None:
        raise KeyError(
            f"badevand GeoJSON has no top-level 'features' array — schema has likely "
            f"changed, do not trust a partial parse. Found keys: {list(data.keys())}"
        )
    sites: list[BathingWaterSite] = []
    for feature in features:
        props = feature.get("properties") or {}
        missing = set(_PROPERTIES.values()) - set(props.keys())
        if missing:
            raise KeyError(
                f"badevand feature missing expected properties {missing} — schema has "
                f"likely changed, do not trust a partial parse. Found: {list(props.keys())}"
            )
        if props[_PROPERTIES["closed"]]:
            continue  # decommissioned station, not a live eligibility signal
        x, y = feature["geometry"]["coordinates"][:2]
        sites.append(
            BathingWaterSite(
                name=props[_PROPERTIES["name"]],
                x=float(x),
                y=float(y),
                is_inland=str(props[_PROPERTIES["water_type"]]).strip().lower() in _INLAND_VALUES,
            )
        )
    inland_count = sum(1 for s in sites if s.is_inland)
    logger.info("loaded %d active badevand sites (%d inland)", len(sites), inland_count)
    return sites


def build_badevand_lookup(sites: list[BathingWaterSite]) -> BadevandLookup:
    """Returns a ``BadevandLookup`` closure: given a lake's record and
    projected geometry, is any inland designated site within
    ``MATCH_RADIUS_M`` of it? Coastal sites are irrelevant here — this
    signal only feeds *lake* eligibility, sea distance doesn't need it."""
    inland_points = [Point(s.x, s.y) for s in sites if s.is_inland]

    def lookup(_lake_record: dict, lake_geom) -> bool:
        return any(pt.distance(lake_geom) <= MATCH_RADIUS_M for pt in inland_points)

    return lookup
