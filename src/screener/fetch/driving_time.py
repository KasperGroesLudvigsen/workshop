"""Per-listing driving time/distance from a single, configurable point of
departure (``DRIVING_ORIGIN_ADDRESS``), via OSRM's public routing demo
server (``router.project-osrm.org``) -- free, no API key.

Batched, not per-listing like ``fetch/photo.py``/``fetch/flood_risk.py``:
OSRM's **Table service** computes a one-to-many time/distance matrix from a
single origin to many destinations in *one* HTTP request, so this fetches
~100 listings' driving times per call instead of one call per listing.
Confirmed live 2026-09-25: a batch of 100 destinations across
Sjælland/Lolland/Falster resolved correctly in under half a second with no
missing routes; a batch of 200 hit OSRM's URL-length limit (HTTP 414) --
100 is comfortably inside it.

OSRM's own usage policy explicitly permits this: "reasonable, non-commercial
use-cases... must not exceed 1 request per second" -- for ~2,400 listings
batched 100-at-a-time, that's ~24 requests for a full run, all rate-limited
to respect that policy. Each listing's own result is still cached
individually (via the project's existing generic ``Database`` raw-response
cache, keyed by origin + destination coordinates), not per-batch, so a
re-run only fetches genuinely new listings regardless of which batch they'd
land in -- the same caching philosophy as every other fetch module here.

The origin address is geocoded through the exact same machinery every other
address in this codebase goes through -- ``find_address_candidates`` (regex)
+ ``DatafordelerAddressValidator`` (real Danish address-register lookup) --
never a raw/unvalidated geocode, and never a silent wrong-origin fallback
for something every listing's distance depends on.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

from screener.db import Database, cache_key
from screener.fetch._rate_limit import RateLimiter
from screener.resolve.address_regex import AddressValidator, find_address_candidates

logger = logging.getLogger(__name__)

OSRM_TABLE_URL = "https://router.project-osrm.org/table/v1/driving"
BATCH_SIZE = 100

NO_ROUTE: dict[str, Any] = {"duration_min": None, "distance_km": None}


def geocode_origin(address_text: str, validator: AddressValidator) -> tuple[float, float]:
    """(lat, lon) for the configured point of departure. Raises if the
    address doesn't parse or doesn't validate -- every listing's driving
    time depends on this being right, so silently falling back to nothing
    would be worse than failing loudly."""
    candidates = find_address_candidates(address_text)
    if not candidates:
        raise ValueError(f"DRIVING_ORIGIN_ADDRESS {address_text!r} doesn't look like a real address")
    validated = validator.validate(candidates[0])
    if validated is None:
        raise ValueError(f"DRIVING_ORIGIN_ADDRESS {address_text!r} failed address-register validation")
    return validated.lat, validated.lon


def origin_address_from_env() -> str:
    address = os.environ.get("DRIVING_ORIGIN_ADDRESS")
    if not address:
        raise RuntimeError("DRIVING_ORIGIN_ADDRESS not set — required for driving-time lookups")
    return address


def _point_cache_key(origin: tuple[float, float], lat: float, lon: float) -> str:
    origin_lat, origin_lon = origin
    return cache_key(
        "driving_time", OSRM_TABLE_URL,
        {"origin_lat": round(origin_lat, 5), "origin_lon": round(origin_lon, 5), "lat": round(lat, 5), "lon": round(lon, 5)},
    )


def _chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _fetch_batch(
    origin: tuple[float, float], batch: list[tuple[str, float, float, str]],
    *, user_agent: str, rate_limiter: RateLimiter, db: Database,
) -> dict[str, dict[str, Any]]:
    origin_lat, origin_lon = origin
    coords = ";".join([f"{origin_lon},{origin_lat}"] + [f"{lon},{lat}" for _id, lat, lon, _key in batch])
    destinations = ";".join(str(i) for i in range(1, len(batch) + 1))
    params = {"sources": "0", "destinations": destinations, "annotations": "duration,distance"}
    url = f"{OSRM_TABLE_URL}/{coords}"

    rate_limiter.wait()
    try:
        resp = requests.get(url, params=params, headers={"User-Agent": user_agent}, timeout=30)
    except requests.RequestException as e:
        logger.warning("driving_time: batch of %d failed: %s", len(batch), e)
        return {}
    if resp.status_code != 200:
        logger.warning("driving_time: batch of %d returned %d", len(batch), resp.status_code)
        return {}

    body = resp.json()
    durations = body["durations"][0]
    distances = body["distances"][0]

    result: dict[str, dict[str, Any]] = {}
    for (listing_id, _lat, _lon, key), duration_s, distance_m in zip(batch, durations, distances):
        value = {
            "duration_min": round(duration_s / 60, 1) if duration_s is not None else None,
            "distance_km": round(distance_m / 1000, 1) if distance_m is not None else None,
        }
        db.save_raw_response(source="driving_time", url=url, params={"listing_id": listing_id}, status_code=200, body=json.dumps(value), key=key)
        result[listing_id] = value
    return result


def fetch_driving_times(
    origin: tuple[float, float], listings: list[dict[str, Any]], *, db: Database, rate_limiter: RateLimiter, user_agent: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    to_fetch: list[tuple[str, float, float, str]] = []
    for listing in listings:
        key = _point_cache_key(origin, listing["lat"], listing["lon"])
        cached = db.get_cached_response(key)
        if cached is not None:
            result[listing["id"]] = json.loads(cached)
        else:
            to_fetch.append((listing["id"], listing["lat"], listing["lon"], key))

    for batch in _chunks(to_fetch, BATCH_SIZE):
        result.update(_fetch_batch(origin, batch, user_agent=user_agent, rate_limiter=rate_limiter, db=db))

    for listing in listings:
        result.setdefault(listing["id"], dict(NO_ROUTE))
    return result
