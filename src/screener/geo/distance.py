"""Point-to-point distance.

Straight-line (haversine) for now. The brief flags this as an open question —
straight-line lies badly around Isefjord and Roskilde Fjord, where the "as
the crow flies" distance across water is much shorter than any real route.
``get_distance_fn`` is the single seam a future drive-time backend plugs
into; nothing else in the codebase should import ``haversine_km`` directly.
"""
from __future__ import annotations

import math
from typing import Callable

EARTH_RADIUS_KM = 6371.0088

DistanceFn = Callable[[float, float, float, float], float]
"""(lon1, lat1, lon2, lat2) -> distance in km."""


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def get_distance_fn(name: str = "straight_line") -> DistanceFn:
    if name == "straight_line":
        return haversine_km
    raise ValueError(f"unknown distance function {name!r} — drive_time backend not implemented yet")
