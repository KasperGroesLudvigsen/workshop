"""Bathing water (badevand) designations — the authoritative signal for
lake swimmability under the brief's eligibility rules.

Source: **Danmarks Miljoeportal's PULS register**, layer ``puls:Badevand``,
served as public GeoServer WFS at ``pulsgeo.miljoeportal.dk`` and published
CC0. This is the register Danish municipalities report into, so it is the
authority for "is this lake an officially designated bathing water", not a
derived or third-party list.

Two other candidate sources were checked and rejected:

- **EMODnet Human Activities** (``emodnet:bathingwaters``) is well
  documented and easy to query, but it is a *marine* portal: all 27,794
  Danish rows are "Coastal Bathing Water", and the layer carries no lake
  sites for any country. Since this signal exists only to judge *lakes*, it
  is exactly useless here — a plausible-looking dead end worth naming so it
  isn't re-tried.
- **Miljoeportal's own "Badevand: Analyse- og Maaleresultater" CSV** is the
  measurement series (E. coli per sample). It carries station ids but no
  coordinates, and its download needs an authenticated portal account. The
  WFS layer used here needs neither.

The register distinguishes ``WaterType`` "Ferskvand" (freshwater — lakes,
146 sites) from "Marin" (1,327) and "Ukendt" (15). Only freshwater sites can
make a lake eligible; the sea distance comes from OSM coastline and needs no
designation. Sites carrying a ``Closed`` date are dropped — a closed bathing
water is not a swimmability signal.

Matching a designated site to an OSM lake polygon is spatial, not
name-based: the official site's coordinate is a sampling point or jetty, not
the lake's centroid, and OSM/official names for the same lake frequently
differ. A site designates a lake if it falls within ``MATCH_RADIUS_M`` of
the lake's polygon.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import requests
from shapely.geometry import Point

from screener.geo.projection import point_to_xy
from screener.geo.water import BadevandLookup

logger = logging.getLogger(__name__)

BADEVAND_WFS_URL = "https://pulsgeo.miljoeportal.dk/geoserver/wfs"
BADEVAND_LAYER = "puls:Badevand"

#: Verified against the live layer (1,488 features): the register's own
#: WaterType vocabulary. Anything not "Ferskvand" cannot make a lake
#: eligible — "Ukendt" included, since an unknown water type is not a
#: positive swimmability signal for a *lake* specifically.
FRESHWATER_VALUE = "Ferskvand"

#: Property names on the WFS features, isolated so a schema change is a
#: one-line fix. Confirmed against DescribeFeatureType.
_PROPERTIES = {"name": "Name", "water_type": "WaterType", "closed": "Closed"}

MATCH_RADIUS_M = 200.0


@dataclass
class BathingWaterSite:
    name: str
    lat: float
    lon: float
    is_inland: bool


def download_badevand_dataset(
    dest_path: Path | str, url: str = BADEVAND_WFS_URL, layer: str = BADEVAND_LAYER
) -> Path:
    """Fetch the register as GeoJSON in WGS84 and cache it to disk.

    Explicit ``srsName`` because the layer's native CRS is not WGS84;
    without it the coordinates come back projected and every downstream
    lat/lon read would be silently wrong rather than failing.
    """
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(
        url,
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeName": layer,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
        },
        timeout=180,
    )
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest


def load_badevand_sites(geojson_path: Path | str) -> list[BathingWaterSite]:
    """Parse cached register GeoJSON into sites, dropping closed stations.

    Raises rather than skipping when the expected properties are absent: a
    schema drift that silently yielded zero inland sites would turn every
    lake ineligible under strict mode, which looks exactly like "no good
    lakes in Denmark" instead of like a bug.
    """
    payload = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
    features = payload.get("features")
    if features is None:
        raise KeyError(f"badevand GeoJSON has no 'features' key — found {sorted(payload)}")

    sites: list[BathingWaterSite] = []
    closed = 0
    for feature in features:
        props = feature.get("properties") or {}
        missing = set(_PROPERTIES.values()) - set(props)
        if missing:
            raise KeyError(
                f"badevand feature missing expected properties {sorted(missing)} — schema has "
                f"likely changed, do not trust a partial parse. Found: {sorted(props)}"
            )
        if props[_PROPERTIES["closed"]]:
            closed += 1
            continue
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates")
        if geometry.get("type") != "Point" or not coordinates:
            continue
        lon, lat = float(coordinates[0]), float(coordinates[1])
        sites.append(
            BathingWaterSite(
                name=props[_PROPERTIES["name"]],
                lat=lat,
                lon=lon,
                is_inland=(props[_PROPERTIES["water_type"]] or "").strip() == FRESHWATER_VALUE,
            )
        )
    inland_count = sum(1 for s in sites if s.is_inland)
    logger.info(
        "loaded %d open badevand sites (%d inland, %d closed sites skipped)",
        len(sites),
        inland_count,
        closed,
    )
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
