"""OSM static prep (stage: static, rare — monthly or on demand).

Downloads the Geofabrik Denmark extract and filters it with pyosmium into a
:class:`screener.geo.store.GeometryStore`: coastline, beach, lake/reservoir
(with area in hectares for the min-lake-area filter), marina, playground,
public pool, and OSM ``leisure=swimming_area`` (a lake-eligibility signal
used in M4). These are geometry, not businesses — the brief notes staleness
isn't a concern here the way it is for CVR/OSM POI businesses.

Two country-extract mirrors are configured. Geofabrik is the default and the
one to prefer when it is reachable; OSM France publishes the same country
extracts and is the fallback for networks where Geofabrik is blocked (it was
unreachable from the environment this was first run in, while OSM France
served the full extract). Either can be passed to
``download_country_extract`` explicitly.

Known simplification: only simple *closed ways* are treated as polygons.
Multipolygon *relations* (a minority of large/complex lakes, assembled from
multiple ways with inner/outer roles) are not assembled here and are
skipped with a count logged — revisit with ``osmium.area.MultipolygonManager``
if this turns out to drop real lakes in the target region.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, NamedTuple

import osmium
import requests
import shapely.geometry as sg

from screener.geo.projection import geom_to_projected
from screener.geo.store import GeometryStore, Layer

logger = logging.getLogger(__name__)

GEOFABRIK_DENMARK_URL = "https://download.geofabrik.de/europe/denmark-latest.osm.pbf"
OSMFR_DENMARK_URL = "https://download.openstreetmap.fr/extracts/europe/denmark.osm.pbf"

#: Tried in order by :func:`download_country_extract`.
DENMARK_EXTRACT_MIRRORS = (GEOFABRIK_DENMARK_URL, OSMFR_DENMARK_URL)


class RawFeature(NamedTuple):
    geom: sg.base.BaseGeometry
    record: dict[str, Any]


def download_country_extract(dest_path: Path | str, urls: tuple[str, ...] = DENMARK_EXTRACT_MIRRORS) -> Path:
    """Download the country extract, trying each mirror in turn.

    Falls through to the next mirror on any network-level failure, and
    raises the last error if every mirror fails — never leaves a partial
    file behind that a later osmium read would choke on.
    """
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for url in urls:
        logger.info("downloading OSM extract from %s", url)
        try:
            with requests.get(url, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
            return dest
        except requests.RequestException as exc:
            logger.warning("mirror %s failed: %s", url, exc)
            dest.unlink(missing_ok=True)
            last_error = exc
    raise RuntimeError(f"every OSM extract mirror failed; last error: {last_error}")


#: Kept as the old name so existing callers/scripts don't break.
download_geofabrik_extract = download_country_extract


def _way_tags(w: "osmium.osm.Way") -> dict[str, str]:
    return {tag.k: tag.v for tag in w.tags}


def _node_tags(n: "osmium.osm.Node") -> dict[str, str]:
    return {tag.k: tag.v for tag in n.tags}


class OsmHandler(osmium.SimpleHandler):
    """Collects raw (WGS84) geometries per category. Call
    ``handler.apply_file(path, locations=True)`` to run it."""

    def __init__(self) -> None:
        super().__init__()
        self.coastline: list[RawFeature] = []
        self.beach: list[RawFeature] = []
        self.lake: list[RawFeature] = []
        self.marina: list[RawFeature] = []
        self.playground: list[RawFeature] = []
        self.pool: list[RawFeature] = []
        self.swimming_area: list[RawFeature] = []
        self.skipped_open_water_ways = 0

    # -- nodes ---------------------------------------------------------

    def node(self, n: "osmium.osm.Node") -> None:
        tags = _node_tags(n)
        if not tags or not n.location.valid():
            return
        point = sg.Point(n.location.lon, n.location.lat)
        name = tags.get("name")
        leisure = tags.get("leisure")
        if leisure == "marina":
            self.marina.append(RawFeature(point, {"name": name, "osm_id": n.id, "kind": "node"}))
        elif leisure == "playground":
            self.playground.append(RawFeature(point, {"name": name, "osm_id": n.id, "kind": "node"}))
        elif leisure == "swimming_pool" and tags.get("access") != "private":
            self.pool.append(RawFeature(point, {"name": name, "osm_id": n.id, "kind": "node"}))
        elif tags.get("natural") == "beach":
            self.beach.append(RawFeature(point, {"name": name, "osm_id": n.id, "kind": "node"}))

    # -- ways ------------------------------------------------------------

    def way(self, w: "osmium.osm.Way") -> None:
        tags = _way_tags(w)
        if not tags:
            return
        try:
            coords = [(nd.lon, nd.lat) for nd in w.nodes if nd.location.valid()]
        except osmium.InvalidLocationError:
            return
        if len(coords) < 2:
            return
        is_closed = len(coords) >= 4 and coords[0] == coords[-1]
        name = tags.get("name")
        record = {"name": name, "osm_id": w.id, "kind": "way"}
        leisure = tags.get("leisure")

        if tags.get("natural") == "coastline":
            self.coastline.append(RawFeature(sg.LineString(coords), record))
        elif tags.get("natural") == "beach":
            geom = sg.Polygon(coords) if is_closed else sg.LineString(coords)
            self.beach.append(RawFeature(geom, record))
        elif (tags.get("natural") == "water" and tags.get("water") == "lake") or tags.get("landuse") == "reservoir":
            if is_closed:
                self.lake.append(RawFeature(sg.Polygon(coords), {**record, "tags": tags}))
            else:
                self.skipped_open_water_ways += 1
        elif leisure == "marina":
            geom = sg.Polygon(coords) if is_closed else sg.LineString(coords)
            self.marina.append(RawFeature(geom, record))
        elif leisure == "playground":
            geom = sg.Polygon(coords) if is_closed else sg.LineString(coords)
            self.playground.append(RawFeature(geom, record))
        elif leisure == "swimming_pool" and tags.get("access") != "private":
            geom = sg.Polygon(coords) if is_closed else sg.LineString(coords)
            self.pool.append(RawFeature(geom, record))
        elif leisure == "swimming_area":
            geom = sg.Polygon(coords) if is_closed else sg.LineString(coords)
            self.swimming_area.append(RawFeature(geom, record))


def _build_layer(features: list[RawFeature], *, compute_area: bool = False) -> Layer:
    geoms = []
    records = []
    for geom, record in features:
        projected = geom_to_projected(geom)
        record = dict(record)
        if compute_area:
            record["area_ha"] = projected.area / 10_000.0
        geoms.append(projected)
        records.append(record)
    return Layer(geoms=geoms, records=records)


def build_geometry_store(osm_path: Path | str) -> GeometryStore:
    handler = OsmHandler()
    handler.apply_file(str(osm_path), locations=True)
    if handler.skipped_open_water_ways:
        logger.warning(
            "skipped %d water/reservoir ways that weren't simple closed ways "
            "(likely multipolygon relation members) — not assembled in this pass",
            handler.skipped_open_water_ways,
        )
    return GeometryStore(
        coastline=_build_layer(handler.coastline),
        beach=_build_layer(handler.beach),
        lake=_build_layer(handler.lake, compute_area=True),
        marina=_build_layer(handler.marina),
        playground=_build_layer(handler.playground),
        pool=_build_layer(handler.pool),
        swimming_area=_build_layer(handler.swimming_area),
    )


def main() -> None:
    import argparse

    from screener.config import REPO_ROOT

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pbf", type=Path, default=REPO_ROOT / "data" / "denmark-latest.osm.pbf")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "osm_store.pkl")
    parser.add_argument("--download", action="store_true", help="download the country extract first")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    if args.download:
        download_country_extract(args.pbf)
    store = build_geometry_store(args.pbf)
    store.save(args.out)
    logger.info("saved geometry store to %s", args.out)


if __name__ == "__main__":
    main()
