from __future__ import annotations

import json
from pathlib import Path

import pytest

from screener.fetch.bathing_water import build_badevand_lookup, load_badevand_sites
from screener.fetch.osm_extract import build_geometry_store
from screener.geo.water import compute_water

OSM_FIXTURE = Path(__file__).parent / "fixtures" / "sample.osm.xml"
BADEVAND_FIXTURE = Path(__file__).parent / "fixtures" / "badevand_sample.geojson"


def test_load_badevand_sites_parses_freshwater_flag():
    sites = load_badevand_sites(BADEVAND_FIXTURE)
    by_name = {s.name: s for s in sites}

    assert by_name["Ensom So Badested"].is_inland is True
    assert by_name["Testhavn Kyst"].is_inland is False


def test_closed_stations_are_dropped():
    """A closed bathing water is not a swimmability signal. The live
    register carries 449 of them against 1,039 open sites, so letting them
    through would materially over-report eligible lakes."""
    names = {s.name for s in load_badevand_sites(BADEVAND_FIXTURE)}

    assert "Nedlagt Sobad" not in names
    assert len(names) == 3


def test_unknown_water_type_is_not_treated_as_freshwater():
    """"Ukendt" is a real value in the register (15 sites). It is not a
    positive signal that a *lake* is swimmable, so it must not count."""
    sites = {s.name: s for s in load_badevand_sites(BADEVAND_FIXTURE)}

    assert sites["Ukendt Vandtype Sted"].is_inland is False


def test_load_badevand_sites_raises_on_missing_properties(tmp_path):
    bad = tmp_path / "bad.geojson"
    bad.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [11.0, 55.0]},
                        "properties": {"Name": "Foo"},
                    }
                ],
            }
        )
    )

    with pytest.raises(KeyError):
        load_badevand_sites(bad)


def test_load_badevand_sites_raises_when_not_a_feature_collection(tmp_path):
    bad = tmp_path / "bad.geojson"
    bad.write_text(json.dumps({"error": "unauthorized"}))

    with pytest.raises(KeyError):
        load_badevand_sites(bad)


def test_real_badevand_lookup_flips_ineligible_lake_to_eligible():
    store = build_geometry_store(OSM_FIXTURE)
    sites = load_badevand_sites(BADEVAND_FIXTURE)
    lookup = build_badevand_lookup(sites)

    lat, lon = 55.426166, 11.518141  # inside "Ensom So", which has no OSM eligibility signal
    result = compute_water(store, lon, lat, min_lake_area_ha=5.0, badevand_lookup=lookup)

    assert result.nearest_lake_any.name == "Ensom So"
    assert result.nearest_lake_any.eligible is True
    assert result.nearest_lake_any.eligibility_reason == "badevand"
    # now strict and loose agree, since the nearest qualifying lake is eligible via badevand
    assert result.open_water_km_strict == result.open_water_km_loose


def test_coastal_sites_never_make_a_lake_eligible():
    store = build_geometry_store(OSM_FIXTURE)
    sites = load_badevand_sites(BADEVAND_FIXTURE)
    lookup = build_badevand_lookup(sites)

    # the coastal-only site is nowhere near "Store Testso"; it already has
    # OSM signals so this mainly checks the coastal site doesn't wrongly
    # short-circuit eligibility for an unrelated lake via some other path.
    lat, lon = 55.435188, 11.559821  # inside "Store Testso"
    result = compute_water(store, lon, lat, min_lake_area_ha=5.0, badevand_lookup=lookup)
    assert result.nearest_lake_any.eligibility_reason in {"swimming_area", "beach_on_shore"}
