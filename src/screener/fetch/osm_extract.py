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

Areas are assembled with osmium's area builder (``FileProcessor.with_areas``)
rather than by treating closed ways as polygons. That distinction is not
cosmetic: Denmark's three largest lakes — Arreso (3,957 ha), Esrum So
(1,735 ha) and Fureso (937 ha) — are all mapped as multipolygon *relations*,
so a way-only extractor drops them. Measured on the real Denmark extract,
way-only found 266 lakes over the 5 ha filter; assembling areas finds 386,
and 410 lakes come from relations. All three missing lakes sit in North
Zealand, inside this project's target postal range.

Because the assembler emits an ``Area`` for every closed way with area tags
*and* for every multipolygon relation, area-shaped features are collected
only from areas; ways contribute only genuinely linear features (coastline,
and beaches/marinas mapped as open ways). Collecting both would double-count
every closed way.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, NamedTuple

import osmium
import requests
import shapely.geometry as sg
import shapely.wkb as swkb

from screener.geo.projection import geom_to_projected
from screener.geo.store import GeometryStore, Layer

logger = logging.getLogger(__name__)

_WKB_FACTORY = osmium.geom.WKBFactory()

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


def _tags(obj) -> dict[str, str]:
    return {tag.k: tag.v for tag in obj.tags}


def _record(name: str | None, osm_id: int, kind: str) -> dict[str, Any]:
    return {"name": name, "osm_id": osm_id, "kind": kind}


def _is_lake(tags: dict[str, str]) -> bool:
    return (tags.get("natural") == "water" and tags.get("water") == "lake") or tags.get(
        "landuse"
    ) == "reservoir"


class _Collector:
    """Accumulates raw (WGS84) geometries per category."""

    def __init__(self) -> None:
        self.coastline: list[RawFeature] = []
        self.beach: list[RawFeature] = []
        self.lake: list[RawFeature] = []
        self.marina: list[RawFeature] = []
        self.playground: list[RawFeature] = []
        self.pool: list[RawFeature] = []
        self.swimming_area: list[RawFeature] = []
        self.unbuildable_areas = 0

    # -- nodes -----------------------------------------------------------

    def add_node(self, n: "osmium.osm.Node") -> None:
        tags = _tags(n)
        if not tags or not n.location.valid():
            return
        point = sg.Point(n.location.lon, n.location.lat)
        record = _record(tags.get("name"), n.id, "node")
        leisure = tags.get("leisure")
        if leisure == "marina":
            self.marina.append(RawFeature(point, record))
        elif leisure == "playground":
            self.playground.append(RawFeature(point, record))
        elif leisure == "swimming_pool" and tags.get("access") != "private":
            self.pool.append(RawFeature(point, record))
        elif tags.get("natural") == "beach":
            self.beach.append(RawFeature(point, record))

    # -- ways ------------------------------------------------------------

    def add_way(self, w: "osmium.osm.Way") -> None:
        """Linear features only.

        A closed way with area tags is emitted separately as an ``Area``, so
        taking it here as well would double-count it. Coastline is the one
        category that is inherently linear and never an area.
        """
        tags = _tags(w)
        if not tags:
            return
        try:
            coords = [(nd.lon, nd.lat) for nd in w.nodes if nd.location.valid()]
        except osmium.InvalidLocationError:
            return
        if len(coords) < 2:
            return
        is_closed = len(coords) >= 4 and coords[0] == coords[-1]
        record = _record(tags.get("name"), w.id, "way")

        if tags.get("natural") == "coastline":
            self.coastline.append(RawFeature(sg.LineString(coords), record))
            return
        if is_closed:
            return  # handled as an area

        line = sg.LineString(coords)
        leisure = tags.get("leisure")
        if tags.get("natural") == "beach":
            self.beach.append(RawFeature(line, record))
        elif leisure == "marina":
            self.marina.append(RawFeature(line, record))
        elif leisure == "playground":
            self.playground.append(RawFeature(line, record))
        elif leisure == "swimming_pool" and tags.get("access") != "private":
            self.pool.append(RawFeature(line, record))
        elif leisure == "swimming_area":
            self.swimming_area.append(RawFeature(line, record))

    # -- areas -----------------------------------------------------------

    def add_area(self, a: "osmium.osm.Area") -> None:
        """Closed ways *and* multipolygon relations, already assembled."""
        tags = _tags(a)
        if not tags:
            return
        leisure = tags.get("leisure")
        if not (
            _is_lake(tags)
            or tags.get("natural") == "beach"
            or leisure in {"marina", "playground", "swimming_area"}
            or (leisure == "swimming_pool" and tags.get("access") != "private")
        ):
            return
        try:
            geom = swkb.loads(_WKB_FACTORY.create_multipolygon(a), hex=True)
        except Exception:
            # Broken rings happen in real extracts; count them rather than
            # dropping them silently, which is the failure this pass exists
            # to fix in the first place.
            self.unbuildable_areas += 1
            return

        # Area ids are synthetic (2*way_id, or 2*relation_id+1); the original
        # id is more useful for tracing a feature back to OSM.
        record = _record(tags.get("name"), a.orig_id(), "way" if a.from_way() else "relation")
        if _is_lake(tags):
            self.lake.append(RawFeature(geom, {**record, "tags": tags}))
        elif tags.get("natural") == "beach":
            self.beach.append(RawFeature(geom, record))
        elif leisure == "marina":
            self.marina.append(RawFeature(geom, record))
        elif leisure == "playground":
            self.playground.append(RawFeature(geom, record))
        elif leisure == "swimming_pool":
            self.pool.append(RawFeature(geom, record))
        elif leisure == "swimming_area":
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
    """Single pass over the extract, assembling areas as it goes.

    Note the input must have ascending ids per object type — every real OSM
    extract does, but a hand-built fixture can easily not, and the assembler
    raises rather than quietly mis-assembling.
    """
    collector = _Collector()
    processor = osmium.FileProcessor(str(osm_path)).with_areas().with_locations()
    for obj in processor:
        if isinstance(obj, osmium.osm.Area):
            collector.add_area(obj)
        elif isinstance(obj, osmium.osm.Node):
            collector.add_node(obj)
        elif isinstance(obj, osmium.osm.Way):
            collector.add_way(obj)

    if collector.unbuildable_areas:
        logger.warning(
            "%d areas could not be assembled into valid geometry and were dropped",
            collector.unbuildable_areas,
        )
    from_relations = sum(1 for _, record in collector.lake if record.get("kind") == "relation")
    logger.info(
        "extracted %d lakes (%d from multipolygon relations), %d coastline, %d beach, "
        "%d marina, %d playground, %d pool, %d swimming_area",
        len(collector.lake), from_relations, len(collector.coastline), len(collector.beach),
        len(collector.marina), len(collector.playground), len(collector.pool),
        len(collector.swimming_area),
    )
    return GeometryStore(
        coastline=_build_layer(collector.coastline),
        beach=_build_layer(collector.beach),
        lake=_build_layer(collector.lake, compute_area=True),
        marina=_build_layer(collector.marina),
        playground=_build_layer(collector.playground),
        pool=_build_layer(collector.pool),
        swimming_area=_build_layer(collector.swimming_area),
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
