"""External map link builders — plain URLs, no API key.

Pin prefers a full address when one is available: opening a bare lat/lon in
Google Maps drops a pin with no name, which is awkward for turn-by-turn
directions or opening in a phone's Maps app, while a real address searches
and navigates cleanly. It falls back to coordinates when a listing has no
address. Aerial and Street View stay coordinate-only regardless — they need
an exact point for the imagery to line up, not a fuzzy address match (a
sommerhusomraade address can geocode a few plots off).

The comma between lat and lon is encoded as ``%2C`` per Google's docs, even
though a bare comma usually works too.
"""
from __future__ import annotations

from urllib.parse import quote


def _latlon(lat: float, lon: float) -> str:
    return f"{lat}%2C{lon}"


def google_pin_url(lat: float, lon: float, *, address: str | None = None) -> str:
    query = quote(address) if address else _latlon(lat, lon)
    return f"https://www.google.com/maps/search/?api=1&query={query}"


def google_aerial_url(lat: float, lon: float, zoom: int = 18) -> str:
    return (
        "https://www.google.com/maps/@?api=1&map_action=map"
        f"&center={_latlon(lat, lon)}&zoom={zoom}&basemap=satellite"
    )


def google_street_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={_latlon(lat, lon)}"


def boligsiden_address_url(slug: str | None) -> str | None:
    """A listing's own boligsiden.dk address page — unlike ``listing_url``
    (a ``/viderestilling/{id}`` redirect straight to the external agent),
    this stays on boligsiden.dk, so it's the page to open to save/follow a
    listing there. ``slug`` comes straight from the Boligsiden API's own
    ``address.slug`` field; ``None`` if a listing doesn't have one."""
    return f"https://www.boligsiden.dk/adresse/{slug}" if slug else None


def build_map_links(
    lat: float, lon: float, *, address: str | None = None, boligsiden_slug: str | None = None
) -> dict[str, str | None]:
    return {
        "google_pin": google_pin_url(lat, lon, address=address),
        "google_aerial": google_aerial_url(lat, lon),
        "google_street": google_street_url(lat, lon),
        "boligsiden_address": boligsiden_address_url(boligsiden_slug),
    }
