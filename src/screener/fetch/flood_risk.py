"""Per-listing coastal flood-risk lookup against Kystdirektoratet/
Miljøstyrelsen's own EU Floods Directive hazard mapping — free, no API key,
served from an ArcGIS Server REST catalog at gisportal.mst.dk. Confirmed
live 2026-09-22: the ``OD_fare_2024`` service's ``identify`` operation
returns a per-return-period ("Hav_Fare_10år" .. "Hav_Fare_10000år") pixel
value at a plain lon/lat point, and ``OD_risikoomraader_2024`` (a vector
layer of officially designated risk-area polygons) tells us whether a point
falls inside an area Kystdirektoratet has actually modeled at all --
needed because the hazard raster only has real data within those areas, so
a location outside one isn't "safe", it's simply unassessed.

DinGeo.dk (the site the "oversvømmelse" feature request referenced) was
considered and rejected: its robots.txt disallows crawlers on its flood-map
endpoints, and its terms of service ban scraping/automated access outright.
This service is the official, direct source instead.

A per-listing on-demand query (like ``fetch/listing_photo.py``), not a
bulk-download-once source (like ``fetch/bathing_water.py``) -- there's no
small file to download, just a point query per listing. Unlike
``listing_photo.py``, every call here hits the *same* server repeatedly
(two calls per listing, one run), so it's rate-limited like the project's
other single-domain API clients (``fetch/_rate_limit.py``), not left
unthrottled the way a "different uncontrolled third-party domain each
time" fetch is.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from screener.db import Database, cache_key
from screener.fetch._rate_limit import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://gisportal.mst.dk/server/rest/services/ekstern"
HAZARD_SERVICE = "OD_fare_2024"
RISK_AREA_SERVICE = "OD_risikoomraader_2024"

_HAV_FARE_RE = re.compile(r"^Hav_Fare_(\d+)år$")

UNASSESSED: dict[str, Any] = {"assessed": False, "shortest_hazard_return_period_years": None}


def _identify(service: str, lon: float, lat: float, *, user_agent: str, rate_limiter: RateLimiter, db: Database) -> dict | None:
    url = f"{BASE_URL}/{service}/MapServer/identify"
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "sr": "4326",
        "tolerance": "1",
        "mapExtent": f"{lon - 0.01},{lat - 0.01},{lon + 0.01},{lat + 0.01}",
        "imageDisplay": "400,400,96",
        "layers": "all",
        "returnGeometry": "false",
        "f": "json",
    }
    key = cache_key(f"flood_risk_{service}", url, {"lon": round(lon, 5), "lat": round(lat, 5)})
    cached = db.get_cached_response(key)
    if cached is not None:
        return json.loads(cached)

    rate_limiter.wait()
    try:
        resp = requests.get(url, params=params, headers={"User-Agent": user_agent}, timeout=15)
    except requests.RequestException as e:
        logger.warning("flood_risk: %s identify failed for (%s, %s): %s", service, lon, lat, e)
        return None
    if resp.status_code != 200:
        logger.warning("flood_risk: %s identify returned %d for (%s, %s)", service, resp.status_code, lon, lat)
        return None

    db.save_raw_response(source=f"flood_risk_{service}", url=url, params={"lon": round(lon, 5), "lat": round(lat, 5)}, status_code=resp.status_code, body=resp.text, key=key)
    return resp.json()


def _parse_hazard_return_period(identify_json: dict) -> int | None:
    years: list[int] = []
    for result in identify_json.get("results", []):
        match = _HAV_FARE_RE.match(result.get("layerName", ""))
        if not match:
            continue
        if result.get("attributes", {}).get("Classify.Pixel Value") == "NoData":
            continue
        years.append(int(match.group(1)))
    return min(years) if years else None


def _parse_is_in_risk_area(identify_json: dict) -> bool:
    return bool(identify_json.get("results"))


def fetch_flood_risk(
    lon: float, lat: float, *, user_agent: str, db: Database, rate_limiter: RateLimiter,
) -> dict[str, Any]:
    hazard_json = _identify(HAZARD_SERVICE, lon, lat, user_agent=user_agent, rate_limiter=rate_limiter, db=db)
    risk_area_json = _identify(RISK_AREA_SERVICE, lon, lat, user_agent=user_agent, rate_limiter=rate_limiter, db=db)
    if hazard_json is None or risk_area_json is None:
        return dict(UNASSESSED)

    assessed = _parse_is_in_risk_area(risk_area_json)
    return {
        "assessed": assessed,
        "shortest_hazard_return_period_years": _parse_hazard_return_period(hazard_json) if assessed else None,
    }
