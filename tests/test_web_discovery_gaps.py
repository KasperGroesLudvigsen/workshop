from __future__ import annotations

from shapely.geometry import Point

from screener.fetch.web_search import TavilyBlockedError, TavilyBudgetExceededError
from screener.geo.projection import point_to_xy
from screener.geo.store import Layer
from screener.resolve.address_regex import AddressCandidate, ValidatedAddress
from screener.resolve.web_discovery_gaps import Gap, fill_gaps, find_gaps, prioritize_gaps

BISSERUP = {"town": "Bisserup", "zip_code": 4243, "lon": 11.4933, "lat": 55.1996}


def _listing(**overrides) -> dict:
    listing = dict(BISSERUP)
    listing.update(overrides)
    return listing


def _layer_at(lon: float, lat: float) -> Layer:
    return Layer(geoms=[Point(*point_to_xy(lon, lat))], records=[{"name": "Some Business"}])


class _FakeValidator:
    def validate(self, candidate: AddressCandidate):
        return ValidatedAddress(street=candidate.street, postal_code=candidate.postal_code, town=candidate.town, lat=55.2, lon=11.5)


class _FakeClient:
    """Stands in for TavilyClient in fill_gaps tests -- discover_via_web_search
    only ever calls ``client.search``."""

    def __init__(self, *, raises=None):
        self.calls_made = 0
        self._raises = raises
        self.queries: list[str] = []

    def search(self, query: str, *, max_results: int = 5):
        self.queries.append(query)
        if self._raises is not None:
            raise self._raises
        self.calls_made += 1
        return []


def test_find_gaps_reports_missing_category():
    directory = {}  # nothing discovered at all yet
    gaps = find_gaps([_listing()], directory, ["hangout"], gap_radius_km=15.0)
    assert gaps == [Gap(town="Bisserup", category="hangout", zip_code="4243")]


def test_find_gaps_reports_too_far_candidate():
    far_layer = _layer_at(11.4933, 55.4996)  # ~33km north -- outside a 15km radius
    directory = {"hangout": far_layer}
    gaps = find_gaps([_listing()], directory, ["hangout"], gap_radius_km=15.0)
    assert len(gaps) == 1
    assert gaps[0].category == "hangout"


def test_find_gaps_reports_no_gap_for_nearby_candidate():
    near_layer = _layer_at(11.4933, 55.1996)  # same point as the listing
    directory = {"hangout": near_layer}
    gaps = find_gaps([_listing()], directory, ["hangout"], gap_radius_km=15.0)
    assert gaps == []


def test_find_gaps_dedupes_by_town_and_category():
    listings = [_listing(), _listing(zip_code=4243)]
    gaps = find_gaps(listings, {}, ["hangout"], gap_radius_km=15.0)
    assert len(gaps) == 1


def test_find_gaps_skips_listings_without_a_town():
    listings = [_listing(town=None)]
    assert find_gaps(listings, {}, ["hangout"], gap_radius_km=15.0) == []


def test_prioritize_gaps_puts_hard_filter_categories_first():
    gaps = [
        Gap(town="Bisserup", category="ice_cream", zip_code="4243"),
        Gap(town="Bisserup", category="hangout", zip_code="4243"),
        Gap(town="Bisserup", category="grocery", zip_code="4243"),
    ]
    ordered = prioritize_gaps(gaps, ("hangout", "grocery"))
    assert [g.category for g in ordered] == ["hangout", "grocery", "ice_cream"]


def _fill_gaps_kwargs(client, **overrides):
    kwargs = dict(
        search_terms={"hangout": "restaurant café kro {town}"},
        client=client,
        validator=_FakeValidator(),
        fetch_page=lambda url: None,  # every result page fails to fetch -> resolved == []
        max_calls_per_run=10,
    )
    kwargs.update(overrides)
    return kwargs


def test_fill_gaps_stops_at_max_calls_per_run():
    gaps = [Gap(town=f"Town{i}", category="hangout", zip_code="4243") for i in range(5)]
    client = _FakeClient()
    fill_gaps(gaps, **_fill_gaps_kwargs(client, max_calls_per_run=2))
    assert client.calls_made == 2
    assert client.queries == ["restaurant café kro Town0 Danmark", "restaurant café kro Town1 Danmark"]


def test_fill_gaps_stops_gracefully_on_budget_exceeded():
    gaps = [Gap(town="Town0", category="hangout", zip_code="4243"), Gap(town="Town1", category="hangout", zip_code="4243")]
    client = _FakeClient(raises=TavilyBudgetExceededError("budget exhausted"))
    # Must not raise -- gap-filling degrades gracefully.
    result = fill_gaps(gaps, **_fill_gaps_kwargs(client))
    assert result == {}


def test_fill_gaps_stops_gracefully_on_blocked():
    gaps = [Gap(town="Town0", category="hangout", zip_code="4243")]
    client = _FakeClient(raises=TavilyBlockedError("blocked"))
    result = fill_gaps(gaps, **_fill_gaps_kwargs(client))
    assert result == {}
