from __future__ import annotations

import pytest

from screener.resolve.address_regex import (
    AddressCandidate,
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
