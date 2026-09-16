from __future__ import annotations

import time

import pytest

from screener.resolve.address_regex import (
    AddressCandidate,
    DatafordelerAddressValidator,
    NotConfiguredValidator,
    ValidatedAddress,
    find_address_candidates,
    resolve_first_valid,
)

TEXT = "Kom forbi og besoeg os pa Havnevej 1, 4243 Rude - vi glaeder os til at se dig!"


class FakeValidator:
    def __init__(self, valid: tuple[str, str, str]):
        self._valid = valid

    def validate(self, candidate: AddressCandidate) -> ValidatedAddress | None:
        if (candidate.street, candidate.postal_code, candidate.town) == self._valid:
            return ValidatedAddress(
                street=candidate.street, postal_code=candidate.postal_code, town=candidate.town,
                lat=55.4, lon=11.5,
            )
        return None


def test_find_address_candidates_matches_danish_shape():
    candidates = find_address_candidates(TEXT)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.street == "Havnevej 1"
    assert c.postal_code == "4243"
    assert c.town == "Rude"


def test_resolve_first_valid_returns_none_when_validator_rejects():
    validator = FakeValidator(("Someone Else Vej 9", "9999", "Nowhere"))
    assert resolve_first_valid(TEXT, validator) is None


def test_resolve_first_valid_returns_validated_address_on_match():
    validator = FakeValidator(("Havnevej 1", "4243", "Rude"))
    result = resolve_first_valid(TEXT, validator)
    assert result is not None
    assert result.lat == 55.4 and result.lon == 11.5


def test_expected_postal_code_filters_candidates():
    validator = FakeValidator(("Havnevej 1", "4243", "Rude"))
    assert resolve_first_valid(TEXT, validator, expected_postal_code="0000") is None


def test_not_configured_validator_raises_rather_than_silently_accepting():
    validator = NotConfiguredValidator()
    with pytest.raises(NotImplementedError):
        validator.validate(AddressCandidate("X", "1234", "Y", "raw"))


class _FakeDarResponse:
    def __init__(self, body: dict):
        self._body = body

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._body


class _FakeDarSession:
    """Stands in for requests.Session: routes the Husnummer query to one
    canned response and the Adressepunkt query to another, by sniffing
    which entity the query text asks for -- mirrors real DAR's two-round-
    trip shape (candidate -> husnummer node -> adressepunkt -> coordinates)."""

    def __init__(self, husnummer_nodes: list[dict], adressepunkt_nodes: list[dict]):
        self._husnummer_nodes = husnummer_nodes
        self._adressepunkt_nodes = adressepunkt_nodes

    def post(self, url, params=None, json=None, timeout=None):
        if "DAR_Husnummer" in json["query"]:
            return _FakeDarResponse({"data": {"DAR_Husnummer": {"nodes": self._husnummer_nodes}}})
        return _FakeDarResponse({"data": {"DAR_Adressepunkt": {"nodes": self._adressepunkt_nodes}}})


# Projects to (659351.73, 6145100.50) in EPSG:25832 -- reused from the
# badevand fixture so the round-trip reprojection has a known-correct answer.
_KNOWN_POINT_WKT = "POINT (659351.7337209156 6145100.50111938)"


def test_datafordeler_validator_accepts_matching_gaeldende_address():
    session = _FakeDarSession(
        husnummer_nodes=[{"adgangsadressebetegnelse": "Havnevej 1, 4243 Rude", "status": "3", "adgangspunkt": "id-1"}],
        adressepunkt_nodes=[{"position": {"wkt": _KNOWN_POINT_WKT}}],
    )
    validator = DatafordelerAddressValidator(api_key="test-key", session=session)
    result = validator.validate(AddressCandidate("Havnevej 1", "4243", "Rude", "raw"))
    assert result is not None
    assert round(result.lat, 3) == 55.426
    assert round(result.lon, 3) == 11.518


def test_datafordeler_validator_rejects_non_gaeldende_status():
    # status "2" is Foreloebig (preliminary), not yet Gaeldende -- must not be trusted.
    session = _FakeDarSession(
        husnummer_nodes=[{"adgangsadressebetegnelse": "Havnevej 1, 4243 Rude", "status": "2", "adgangspunkt": "id-1"}],
        adressepunkt_nodes=[],
    )
    validator = DatafordelerAddressValidator(api_key="test-key", session=session)
    assert validator.validate(AddressCandidate("Havnevej 1", "4243", "Rude", "raw")) is None


def test_datafordeler_validator_rejects_postal_code_mismatch():
    # same street name matched by startsWith, but a different town/postal --
    # DAR's own postnummer field is an opaque reference id, not filterable
    # server-side, so this cross-check has to happen client-side.
    session = _FakeDarSession(
        husnummer_nodes=[{"adgangsadressebetegnelse": "Havnevej 1, 9999 Andenby", "status": "3", "adgangspunkt": "id-1"}],
        adressepunkt_nodes=[],
    )
    validator = DatafordelerAddressValidator(api_key="test-key", session=session)
    assert validator.validate(AddressCandidate("Havnevej 1", "4243", "Rude", "raw")) is None


def test_datafordeler_validator_raises_on_graphql_error():
    class _ErrorSession:
        def post(self, url, params=None, json=None, timeout=None):
            return _FakeDarResponse({"errors": [{"message": "boom"}], "data": None})

    validator = DatafordelerAddressValidator(api_key="test-key", session=_ErrorSession())
    with pytest.raises(RuntimeError):
        validator.validate(AddressCandidate("Havnevej 1", "4243", "Rude", "raw"))


def test_datafordeler_validator_requires_api_key(monkeypatch):
    monkeypatch.delenv("DATAFORDELER_DAR_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        DatafordelerAddressValidator(api_key=None)


def test_datafordeler_validator_rate_limits_requests():
    # 2 calls/sec -> the 2 real HTTP calls one validate() makes (Husnummer,
    # then Adressepunkt) must be spaced at least ~0.5s apart.
    session = _FakeDarSession(
        husnummer_nodes=[{"adgangsadressebetegnelse": "Havnevej 1, 4243 Rude", "status": "3", "adgangspunkt": "id-1"}],
        adressepunkt_nodes=[{"position": {"wkt": _KNOWN_POINT_WKT}}],
    )
    validator = DatafordelerAddressValidator(api_key="test-key", session=session, requests_per_second=2.0)
    start = time.monotonic()
    validator.validate(AddressCandidate("Havnevej 1", "4243", "Rude", "raw"))
    elapsed = time.monotonic() - start
    assert elapsed >= 0.4  # allow scheduling slack below the 0.5s floor
