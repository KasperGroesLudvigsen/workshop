"""Per-listing coastal flood-risk lookup against Kystdirektoratet's
"Kystplanlægger 2120" data — free, no API key, served from the same
gisportal.mst.dk ArcGIS Server REST catalog as everything else in this
module's history.

An earlier version of this module queried `OD_fare_2024`/
`OD_risikoomraader_2024` (the EU Floods Directive risk-area mapping), but
that only covers ~51 officially designated risk-area municipalities --
confirmed live against the real generated site, ~90% of listings came back
"not assessed", since most summer houses sit in small coastal hamlets never
in scope for that narrower, city-focused regulatory dataset.

`Kystplanlaegger_Oversvommelsesfare_2` is Kystdirektoratet's own nationwide
coastal-risk screening tool instead -- built to assess the *entire* Danish
coast to the year 2120, not just designated risk areas. Confirmed live: real
listings that were "not assessed" under the old service came back with real
hazard/depth data under this one, while a deliberately-inland test point
correctly came back empty of hazard hits.

Only two states are reported, not three: "flood risk found" (a return
period + depth) or "no flood risk found". A separate "not assessed" state
was tried and dropped -- confirmed live that a genuinely safe, modeled
coastal point and a random inland point produce the *identical* signature
here (no hazard polygon hit, every depth raster value "NoData"), unlike the
old service, which had a dedicated designated-risk-area polygon layer to
tell the two apart. Claiming "assessed, no risk" here would not be honest
about what this data can actually distinguish.

One `identify` call per listing returns everything: for 3 climate horizons
(2020/2070/2120) x 4 storm-surge return periods (50/100/1,000/10,000
years), both a vector "hazard" polygon layer (is this point inside the
modeled flood extent) and a parallel raster "depth" layer (actual flood
depth in metres where flooded). Confirmed live that a *tight* mapExtent/low
tolerance can make the vector hazard layer miss a point sitting right at a
flood-zone boundary even though the raster depth layer still returns a real
value there -- the query below uses a wider window (matching a manually
verified working query), which resolved every case tested.

DinGeo.dk (the site the "oversvømmelse" feature request referenced) was
considered and rejected: its robots.txt disallows crawlers on its flood-map
endpoints, and its terms of service ban scraping/automated access outright.
This service is the official, direct source instead.

A per-listing on-demand query (like ``fetch/listing_photo.py``), not a
bulk-download-once source (like ``fetch/bathing_water.py``) -- there's no
small file to download, just a point query per listing. Every call here
hits the *same* server repeatedly, so it's rate-limited like the project's
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
HAZARD_SERVICE = "Kystplanlaegger_Oversvommelsesfare_2"

# Layer-name formatting is inconsistent across return periods in the source
# data (confirmed live) -- "50-års" vs "100 år" vs "1.000 år" -- so the
# separator before "år" and the trailing "s" are both optional/either-of.
_LAYER_RE = re.compile(r"^Oversvømmelses(fare|dybde) i (\d{4}) - ([\d.]+)[-\s]års? hændelse$")

CURRENT_YEAR = "2020"
FUTURE_YEAR = "2120"

NO_RISK_FOUND: dict[str, Any] = {
    "current_return_period_years": None,
    "current_depth_m": None,
    "2120_return_period_years": None,
    "2120_depth_m": None,
}


def _identify(lon: float, lat: float, *, user_agent: str, rate_limiter: RateLimiter, db: Database) -> dict | None:
    url = f"{BASE_URL}/{HAZARD_SERVICE}/MapServer/identify"
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "sr": "4326",
        "tolerance": "5",
        "mapExtent": f"{lon - 0.1},{lat - 0.1},{lon + 0.1},{lat + 0.1}",
        "imageDisplay": "800,800,96",
        "layers": "all",
        "returnGeometry": "false",
        "f": "json",
    }
    key = cache_key(f"flood_risk_{HAZARD_SERVICE}", url, {"lon": round(lon, 5), "lat": round(lat, 5)})
    cached = db.get_cached_response(key)
    if cached is not None:
        return json.loads(cached)

    rate_limiter.wait()
    try:
        resp = requests.get(url, params=params, headers={"User-Agent": user_agent}, timeout=15)
    except requests.RequestException as e:
        logger.warning("flood_risk: identify failed for (%s, %s): %s", lon, lat, e)
        return None
    if resp.status_code != 200:
        logger.warning("flood_risk: identify returned %d for (%s, %s)", resp.status_code, lon, lat)
        return None

    db.save_raw_response(source=f"flood_risk_{HAZARD_SERVICE}", url=url, params={"lon": round(lon, 5), "lat": round(lat, 5)}, status_code=resp.status_code, body=resp.text, key=key)
    return resp.json()


def _parse_scenarios(identify_json: dict) -> dict[str, dict[int, dict[str, Any]]]:
    """{"2020": {50: {"hazard": True, "depth_m": 0.32}, 100: {...}, ...}, "2070": {...}, "2120": {...}}"""
    scenarios: dict[str, dict[int, dict[str, Any]]] = {}
    for result in identify_json.get("results", []):
        match = _LAYER_RE.match(result.get("layerName", ""))
        if not match:
            continue
        kind, year, return_period_text = match.groups()
        return_period = int(return_period_text.replace(".", ""))
        scenario = scenarios.setdefault(year, {}).setdefault(return_period, {"hazard": False, "depth_m": None})
        if kind == "fare":
            # A vector feature only appears in `results` at all when the
            # point falls inside its polygon -- presence is the signal.
            scenario["hazard"] = True
        else:
            pixel_value = result.get("attributes", {}).get("Classify.Pixel Value")
            if pixel_value not in (None, "NoData"):
                scenario["depth_m"] = float(pixel_value)
                scenario["hazard"] = True  # a real depth reading is itself evidence of hazard,
                # independent of (and occasionally more reliable at a flood-zone boundary than)
                # the separately-vectorized polygon layer above.
    return scenarios


def _summarize_year(scenarios: dict[str, dict[int, dict[str, Any]]], year: str) -> tuple[int | None, float | None]:
    year_scenarios = scenarios.get(year, {})
    hazard_periods = [rp for rp, s in year_scenarios.items() if s["hazard"]]
    if not hazard_periods:
        return None, None
    shortest = min(hazard_periods)
    return shortest, year_scenarios[shortest]["depth_m"]


def fetch_flood_risk(
    lon: float, lat: float, *, user_agent: str, db: Database, rate_limiter: RateLimiter,
) -> dict[str, Any]:
    identify_json = _identify(lon, lat, user_agent=user_agent, rate_limiter=rate_limiter, db=db)
    if identify_json is None:
        return dict(NO_RISK_FOUND)

    scenarios = _parse_scenarios(identify_json)
    current_years, current_depth = _summarize_year(scenarios, CURRENT_YEAR)
    future_years, future_depth = _summarize_year(scenarios, FUTURE_YEAR)
    return {
        "current_return_period_years": current_years,
        "current_depth_m": current_depth,
        "2120_return_period_years": future_years,
        "2120_depth_m": future_depth,
    }
