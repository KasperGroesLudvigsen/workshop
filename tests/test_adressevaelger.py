"""Tests for the Adressevaelger-backed address validator.

Driven entirely through the ``transport`` seam against
``fixtures/adressevaelger_sample.json`` — real responses captured from the
live service, so the parsing is tested against the shape the API actually
returns rather than an invented one, without any test touching the network.

The tests that matter most here are the rejection cases. The search is fuzzy
by design, so the validator's whole job is refusing plausible-but-wrong
hits; a validator that only ever said "yes" would pass a naive happy-path
test and quietly geocode businesses into the wrong towns.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from screener.resolve.address_regex import AddressCandidate
from screener.resolve.adressevaelger import (
    AdressevaelgerError,
    AdressevaelgerValidator,
    _house_number,
)

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "adressevaelger_sample.json").read_text())

# The Copenhagen NV husnummer that "Rentemestervej 8" resolves to, and the
# Hilleroed adresse that the same search also returns.
_HUSNUMMER_ID = "0a3f507a-e179-32b8-e044-0003ba298018"
_ADRESSE_ID = "0a3f50a9-cc30-32b8-e044-0003ba298018"


def make_validator(*, search_body=None):
    """Validator wired to the captured fixtures, recording every call."""
    calls: list[tuple[str, dict]] = []
    search = FIXTURES["soeg_rentemestervej"] if search_body is None else search_body

    def transport(url: str, params: dict):
        calls.append((url, params))
        if url.endswith("/adresser/soeg"):
            return search
        if url.endswith(f"/husnumre/{_HUSNUMMER_ID}"):
            return FIXTURES["husnummer_record"]
        if url.endswith(f"/adresser/{_ADRESSE_ID}"):
            return FIXTURES["adresse_record"]
        raise AssertionError(f"unexpected request to {url}")

    validator = AdressevaelgerValidator(transport=transport, requests_per_second=0)
    return validator, calls


def candidate(street="Rentemestervej 8", postal_code="2400", town="København NV"):
    return AddressCandidate(street=street, postal_code=postal_code, town=town, raw_text="t")


def test_validates_and_returns_wgs84_coordinates():
    validator, _ = make_validator()

    result = validator.validate(candidate())

    assert result is not None
    assert result.street == "Rentemestervej 8"
    assert result.postal_code == "2400"
    assert result.town == "København NV"
    # The register stores EPSG:25832 (722125.86, 6178892.29); these are the
    # WGS84 degrees that projects back to, cross-checked against the same
    # address in the national address register.
    assert result.lat == pytest.approx(55.7048, abs=1e-3)
    assert result.lon == pytest.approx(12.5355, abs=1e-3)


def test_rejects_correct_street_in_wrong_postal_code():
    """The search returns Rentemestervej 8 in *both* 2400 and 3400. Asking
    for a third postal code must not quietly accept either of them."""
    validator, _ = make_validator()

    assert validator.validate(candidate(postal_code="4180", town="Sorø")) is None


def test_picks_the_hit_matching_the_requested_postal_code():
    """Same street and number, two towns — the postal code decides which."""
    validator, _ = make_validator()

    result = validator.validate(candidate(postal_code="3400", town="Hillerød"))

    assert result is not None
    assert result.postal_code == "3400"
    assert result.town == "Hillerød"
    # Hilleroed is ~27 km north-west of the Copenhagen hit; a validator that
    # returned the wrong one would still look "valid" without this check.
    assert result.lat == pytest.approx(55.9447, abs=1e-3)


def test_rejects_wrong_house_number_on_the_right_street_and_town():
    """The fuzzy search answers a house number it has no match for with the
    street's *other* numbers — here, asking for number 9 returns number 8 in
    the right town. Street and postal code both agree; only the door does
    not, and the gate is on the door."""
    hits = [h for h in FIXTURES["soeg_rentemestervej"]["fund"] if h["id"] == _HUSNUMMER_ID]
    validator, _ = make_validator(search_body={"status": "ok", "beskrivelse": "", "fund": hits})

    assert validator.validate(candidate(street="Rentemestervej 9")) is None


def test_rejects_candidate_without_a_house_number_without_calling_out():
    validator, calls = make_validator()

    assert validator.validate(candidate(street="Rentemestervej")) is None
    assert calls == []


def test_tries_both_query_forms_before_giving_up():
    """The postal-code form and the town-only form fail in complementary
    ways against the live service, so a miss on the first must fall through
    to the second rather than ending the search."""
    validator, calls = make_validator(search_body={"status": "ok", "beskrivelse": "", "fund": []})

    assert validator.validate(candidate()) is None

    queries = [params["tekst"] for _, params in calls]
    assert queries == ["Rentemestervej 8, 2400 København NV", "Rentemestervej 8 København NV"]


def test_short_circuits_on_the_first_query_that_validates():
    validator, calls = make_validator()

    assert validator.validate(candidate()) is not None

    searches = [params["tekst"] for url, params in calls if url.endswith("/adresser/soeg")]
    assert searches == ["Rentemestervej 8, 2400 København NV"]


def test_service_error_raises_rather_than_reading_as_no_match():
    """A bad token or a broken service must not look like "this business
    doesn't exist" — that would empty the directory silently."""
    validator, _ = make_validator(
        search_body={"status": "fejl", "beskrivelse": "ugyldigt token", "fund": []}
    )

    with pytest.raises(AdressevaelgerError, match="ugyldigt token"):
        validator.validate(candidate())


def test_unexpected_response_shape_raises():
    validator, _ = make_validator(search_body={"status": "ok"})

    with pytest.raises(AdressevaelgerError, match="unexpected search response shape"):
        validator.validate(candidate())


def test_token_is_sent_on_every_request():
    validator, calls = make_validator()

    validator.validate(candidate())

    assert calls and all(params.get("token") == "adressevaelger123" for _, params in calls)


def test_street_level_hits_are_skipped_without_a_detail_call():
    """A `vejnavn` hit has no address to validate; asking for its record
    would 400."""
    validator, calls = make_validator(
        search_body={
            "status": "ok",
            "beskrivelse": "",
            "fund": [{"type": "vejnavn", "id": "x", "titel": "Rentemestervej, 2400 København NV"}],
        }
    )

    assert validator.validate(candidate()) is None
    assert all(url.endswith("/adresser/soeg") for url, _ in calls)


@pytest.mark.parametrize(
    "street,expected",
    [
        ("Hovedgaden 12", "12"),
        ("Hovedgaden 12B", "12b"),
        ("H. P. Christensensvej 15", "15"),
        ("Hovedgaden", None),
    ],
)
def test_house_number_extraction(street, expected):
    assert _house_number(street) == expected
