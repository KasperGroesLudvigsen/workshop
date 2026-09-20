from __future__ import annotations

from pathlib import Path

from screener.fetch.osm_poi import extract_business_pois

FIXTURE = Path(__file__).parent / "fixtures" / "business_pois.osm.xml"


def test_finds_bisserup_strand_kro_and_grillhus_under_hangout():
    result = extract_business_pois(FIXTURE)
    names = {b.name for b in result["hangout"]}
    assert "Bisserup Strand Kro" in names
    assert "Bisserup Is og Grillhus" in names


def test_resolved_business_has_no_address_but_real_coordinates():
    result = extract_business_pois(FIXTURE)
    kro = next(b for b in result["hangout"] if b.name == "Bisserup Strand Kro")
    assert kro.source_step == "osm_poi"
    assert kro.street == "" and kro.postal_code == "" and kro.town == ""
    assert round(kro.lat, 3) == 55.200
    assert round(kro.lon, 3) == 11.493


def test_finds_named_shop_under_grocery():
    result = extract_business_pois(FIXTURE)
    names = {b.name for b in result["grocery"]}
    assert "Dagli'Brugsen" in names


def test_way_polygon_resolves_to_a_centroid():
    result = extract_business_pois(FIXTURE)
    way_shop = next(b for b in result["grocery"] if b.name == "Way Shop")
    assert 55.209 < way_shop.lat < 55.211
    assert 11.499 < way_shop.lon < 11.501


def test_unnamed_poi_is_skipped():
    result = extract_business_pois(FIXTURE)
    all_names = {b.name for cat in result.values() for b in cat}
    assert "" not in all_names
    # the unnamed cafe (id=401) contributes nothing; only named hangout POIs count
    assert len(result["hangout"]) == 2


def test_irrelevant_tag_is_ignored():
    result = extract_business_pois(FIXTURE)
    all_names = {b.name for cat in result.values() for b in cat}
    assert "Irrelevant Parking" not in all_names
