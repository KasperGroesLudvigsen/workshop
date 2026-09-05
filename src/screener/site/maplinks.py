"""External map link builders — plain URLs, no API key.

Coordinates, not addresses: a sommerhusomraade address geocodes unreliably
and can land on the wrong plot, while Boliga gives lat/lon directly. The
comma between lat and lon is encoded as ``%2C`` per Google's docs, even
though a bare comma usually works too.
"""
from __future__ import annotations

from urllib.parse import quote


def _latlon(lat: float, lon: float) -> str:
    return f"{lat}%2C{lon}"


def google_pin_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={_latlon(lat, lon)}"


def google_aerial_url(lat: float, lon: float, zoom: int = 18) -> str:
    return (
        "https://www.google.com/maps/@?api=1&map_action=map"
        f"&center={_latlon(lat, lon)}&zoom={zoom}&basemap=satellite"
    )


def google_street_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={_latlon(lat, lon)}"


def apple_maps_url(lat: float, lon: float, address: str | None = None) -> str:
    url = f"https://maps.apple.com/?ll={_latlon(lat, lon)}"
    if address:
        url += f"&q={quote(address)}"
    return url


def build_map_links(lat: float, lon: float, address: str | None = None) -> dict[str, str]:
    return {
        "google_pin": google_pin_url(lat, lon),
        "google_aerial": google_aerial_url(lat, lon),
        "google_street": google_street_url(lat, lon),
        "apple": apple_maps_url(lat, lon, address),
    }
