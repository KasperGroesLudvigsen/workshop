from __future__ import annotations

from pathlib import Path

from screener.fetch.osm_extract import build_geometry_store
from screener.geo.water import compute_water

FIXTURE = Path(__file__).parent / "fixtures" / "sample.osm.xml"
MIN_LAKE_AREA_HA = 5.0


def _store():
    return build_geometry_store(FIXTURE)


def test_pond_below_min_area_is_never_a_candidate():
    store = _store()
    lat, lon = 55.431729, 11.553273  # right at the pond (0.25 ha)
    result = compute_water(store, lon, lat, min_lake_area_ha=MIN_LAKE_AREA_HA)
    assert result.nearest_lake_any is not None
    assert result.nearest_lake_any.name != "Lille Dam"
    assert result.nearest_lake_any.name == "Store Testso"


def test_eligible_lake_via_swimming_area_and_beach():
    store = _store()
    lat, lon = 55.435188, 11.559821  # inside the big lake
    result = compute_water(store, lon, lat, min_lake_area_ha=MIN_LAKE_AREA_HA)
    assert result.nearest_lake_any is not None
    assert result.nearest_lake_any.name == "Store Testso"
    assert result.nearest_lake_any.eligible is True
    assert result.nearest_lake_any.eligibility_reason in {"swimming_area", "beach_on_shore"}
    assert result.nearest_eligible_lake is not None
    assert result.nearest_eligible_lake.name == "Store Testso"
    # strict and loose agree when the nearest qualifying lake is itself eligible
    assert result.open_water_km_strict == result.open_water_km_loose


def test_strict_vs_loose_diverge_near_an_ineligible_lake():
    store = _store()
    lat, lon = 55.426166, 11.518141  # inside the isolated, ineligible lake
    result = compute_water(store, lon, lat, min_lake_area_ha=MIN_LAKE_AREA_HA, search_radius_km=20.0)

    assert result.nearest_lake_any is not None
    assert result.nearest_lake_any.name == "Ensom So"
    assert result.nearest_lake_any.eligible is False
    assert result.nearest_lake_any.eligibility_reason == "none"

    assert result.nearest_eligible_lake is not None
    assert result.nearest_eligible_lake.name == "Store Testso"
    assert result.nearest_eligible_lake.eligible is True

    # loose uses the near ineligible lake; strict has to reach further for
    # an eligible one, so strict's open-water distance is larger.
    assert result.open_water_km_loose < result.open_water_km_strict


def test_badevand_lookup_overrides_osm_signals():
    store = _store()
    lat, lon = 55.426166, 11.518141  # the isolated lake, no OSM eligibility signal

    def badevand_lookup(record, geom):
        return record.get("name") == "Ensom So"

    result = compute_water(
        store, lon, lat, min_lake_area_ha=MIN_LAKE_AREA_HA, badevand_lookup=badevand_lookup
    )
    assert result.nearest_lake_any.eligible is True
    assert result.nearest_lake_any.eligibility_reason == "badevand"
    assert result.nearest_eligible_lake.name == "Ensom So"
    assert result.open_water_km_strict == result.open_water_km_loose


def test_sea_distance_is_always_present():
    store = _store()
    lat, lon = 55.4082577, 11.5580782  # near the synthetic coastline
    result = compute_water(store, lon, lat, min_lake_area_ha=MIN_LAKE_AREA_HA)
    assert result.sea_distance_km < 1.0
