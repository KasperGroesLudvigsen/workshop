"""Free, key-less business discovery via OpenStreetMap POI tags — the
primary mechanism for M7 (see docs/HANDOFF.md): a business already placed
as real geometry in a structured, community-maintained dataset needs no
address-text validation at all, unlike CVR/web-search discovery, which
both start from raw text that could be wrong.

Confirmed live 2026-09-19 against the public Overpass API before this
module was written: a query for named amenities near Bisserup returned
both "Bisserup Strand Kro" and "Bisserup Is og Grillhus" by their real
names, with exact coordinates — neither is CVR-discoverable (see
docs/HANDOFF.md's CVR structural-blind-spot finding: the kro is legally
run through a property-holding company registered under a non-hospitality
branch code in a different town entirely).

Runs its own separate pyosmium pass over the already-downloaded Denmark
extract (`fetch/osm_extract.py`'s `download_geofabrik_extract`) rather
than extending that module's own `OsmHandler`, to avoid any risk to the
existing, tested `GeometryStore`-building code — the cost is one more
multi-minute pass over the PBF, not a risk worth taking against working
code.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import osmium
import shapely.geometry as sg

from screener.resolve.pipeline import ResolvedBusiness

logger = logging.getLogger(__name__)

# OSM tag value -> our existing category name (score/pipeline.py's
# CVR_CATEGORIES). Two separate dicts (amenity vs. shop) since that's how
# OSM itself splits these, not because the values could collide.
_AMENITY_CATEGORY = {
    "restaurant": "hangout",
    "fast_food": "hangout",
    "cafe": "hangout",
    "bar": "hangout",
    "pub": "hangout",
    "ice_cream": "ice_cream",
}
_SHOP_CATEGORY = {
    "supermarket": "grocery",
    "convenience": "grocery",
    "grocery": "grocery",
    "seafood": "fish_shop",
    "alcohol": "wine_shop",
    "wine": "wine_shop",
    "confectionery": "ice_cream",
    "butcher": "butcher",
}


def _tags(obj: Any) -> dict[str, str]:
    return {t.k: t.v for t in obj.tags}


def _category_for(tags: dict[str, str]) -> str | None:
    if tags.get("amenity") in _AMENITY_CATEGORY:
        return _AMENITY_CATEGORY[tags["amenity"]]
    if tags.get("shop") in _SHOP_CATEGORY:
        return _SHOP_CATEGORY[tags["shop"]]
    return None


class _BusinessPoiHandler(osmium.SimpleHandler):
    def __init__(self) -> None:
        super().__init__()
        self.by_category: dict[str, list[tuple[str, float, float]]] = {}

    def _add(self, category: str, name: str, lon: float, lat: float) -> None:
        self.by_category.setdefault(category, []).append((name, lon, lat))

    def node(self, n: "osmium.osm.Node") -> None:
        tags = _tags(n)
        name = tags.get("name")
        if not name or not n.location.valid():
            return
        category = _category_for(tags)
        if category:
            self._add(category, name, n.location.lon, n.location.lat)

    def way(self, w: "osmium.osm.Way") -> None:
        tags = _tags(w)
        name = tags.get("name")
        if not name:
            return
        category = _category_for(tags)
        if not category:
            return
        try:
            coords = [(nd.lon, nd.lat) for nd in w.nodes if nd.location.valid()]
        except osmium.InvalidLocationError:
            return
        if len(coords) < 2:
            return
        is_closed = len(coords) >= 4 and coords[0] == coords[-1]
        centroid = sg.Polygon(coords).centroid if is_closed else sg.LineString(coords).centroid
        self._add(category, name, centroid.x, centroid.y)


def extract_business_pois(osm_path: Path | str) -> dict[str, list[ResolvedBusiness]]:
    """Returns category -> list of ResolvedBusiness, one per named POI
    matching that category's OSM tags, with `source_step="osm_poi"` and no
    street/postal_code/town (OSM gives coordinates directly — nothing to
    validate an address against)."""
    handler = _BusinessPoiHandler()
    handler.apply_file(str(osm_path), locations=True)
    result: dict[str, list[ResolvedBusiness]] = {}
    for category, entries in handler.by_category.items():
        result[category] = [
            ResolvedBusiness(name=name, street="", postal_code="", town="", lat=lat, lon=lon, source_step="osm_poi")
            for name, lon, lat in entries
        ]
        logger.info("osm_poi: found %d %s POI(s)", len(result[category]), category)
    return result
