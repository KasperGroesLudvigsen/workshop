from __future__ import annotations

from pathlib import Path

from shapely.geometry import Point

from screener.fetch.osm_extract import build_geometry_store
from screener.geo.projection import point_to_xy

FIXTURE = Path(__file__).parent / "fixtures" / "sample.osm.xml"


def test_build_geometry_store_extracts_all_categories():
    store = build_geometry_store(FIXTURE)
    assert len(store.coastline) == 1
    assert len(store.beach) == 1
    assert len(store.lake) == 3  # big lake + small pond + isolated ineligible lake
    assert len(store.marina) == 1
    assert len(store.playground) == 1
    assert len(store.pool) == 1
    assert len(store.swimming_area) == 1


def test_lake_area_ha_computed_and_filters_small_pond():
    store = build_geometry_store(FIXTURE)
    areas = {r["name"]: r["area_ha"] for _, r in zip(store.lake.geoms, store.lake.records)}
    assert areas["Store Testso"] == _approx(9.0, tol=0.5)
    assert areas["Lille Dam"] == _approx(0.25, tol=0.05)


def _approx(expected, tol):
    class _Match:
        def __eq__(self, other):
            return abs(other - expected) <= tol
    return _Match()


def test_nearest_and_within_queries_work_in_projected_meters():
    store = build_geometry_store(FIXTURE)
    # query point near the lake (roughly its centroid)
    x, y = point_to_xy(11.5598, 55.4352)
    pt = Point(x, y)

    lake_hit = store.lake.nearest(pt)
    assert lake_hit is not None
    record, dist_m, _geom = lake_hit
    assert record["name"] == "Store Testso"
    assert dist_m < 50  # inside/near the lake polygon

    marina_within = store.marina.within(pt, radius_m=4000)
    assert len(marina_within) == 1
    assert marina_within[0][0]["name"] == "Testhavn Marina"

    # coastline is ~3km south of the lake; within a 2km radius should find nothing
    assert store.coastline.within(pt, radius_m=2000) == []
    coast_hit = store.coastline.nearest(pt)
    assert coast_hit is not None
    assert coast_hit[1] > 2000


def test_swimming_area_overlaps_the_big_lake():
    store = build_geometry_store(FIXTURE)
    lake_geom = store.lake.geoms[[r["name"] for r in store.lake.records].index("Store Testso")]
    swim_geom = store.swimming_area.geoms[0]
    assert lake_geom.intersects(swim_geom)


def test_beach_shares_shore_with_lake():
    store = build_geometry_store(FIXTURE)
    lake_geom = store.lake.geoms[[r["name"] for r in store.lake.records].index("Store Testso")]
    beach_geom = store.beach.geoms[0]
    # the beach way runs along the lake's boundary -> should be touching/very close
    assert lake_geom.distance(beach_geom) < 1.0
