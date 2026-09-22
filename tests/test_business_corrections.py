from __future__ import annotations

from pathlib import Path

from screener.resolve.address_regex import AddressCandidate, ValidatedAddress
from screener.resolve.business_corrections import (
    BusinessCorrections,
    ExcludeRule,
    RelocateRule,
    apply_corrections,
    load_corrections,
)
from screener.resolve.pipeline import ResolvedBusiness


def _business(name: str, street="Vej 1", postal_code="4000", town="Town", lat=55.0, lon=11.0) -> ResolvedBusiness:
    return ResolvedBusiness(name=name, street=street, postal_code=postal_code, town=town, lat=lat, lon=lon, source_step="cvr_discovery")


class _FakeValidator:
    def __init__(self, accept: tuple[str, str, str], result: ValidatedAddress):
        self._accept = accept
        self._result = result

    def validate(self, candidate: AddressCandidate) -> ValidatedAddress | None:
        if (candidate.street, candidate.postal_code, candidate.town) == self._accept:
            return self._result
        return None


def test_load_corrections_returns_empty_when_file_missing(tmp_path: Path):
    corrections = load_corrections(tmp_path / "does-not-exist.yaml")
    assert corrections == BusinessCorrections()


def test_load_corrections_parses_yaml(tmp_path: Path):
    path = tmp_path / "corrections.yaml"
    path.write_text(
        """
exclude:
  - name: "Bad Kro"
    reason: "closed"
relocate:
  - name: "Misplaced Cafe"
    street: "Rigtig Vej 1"
    postal_code: "4000"
    town: "Rigtig By"
""",
        encoding="utf-8",
    )
    corrections = load_corrections(path)
    assert corrections.exclude == [ExcludeRule(name="Bad Kro", reason="closed")]
    assert corrections.relocate == [RelocateRule(name="Misplaced Cafe", street="Rigtig Vej 1", postal_code="4000", town="Rigtig By")]


def test_apply_corrections_excludes_by_name_case_and_whitespace_insensitive():
    businesses = {"hangout": [_business("Cafe JaTak ApS"), _business("Kept Kro")]}
    corrections = BusinessCorrections(exclude=[ExcludeRule(name="  cafe jatak aps  ")])
    result = apply_corrections(businesses, corrections, validator=_FakeValidator(("", "", ""), None))
    assert [b.name for b in result["hangout"]] == ["Kept Kro"]


def test_apply_corrections_excludes_across_every_category():
    businesses = {"hangout": [_business("Bad Biz")], "grocery": [_business("Bad Biz")]}
    corrections = BusinessCorrections(exclude=[ExcludeRule(name="Bad Biz")])
    result = apply_corrections(businesses, corrections, validator=_FakeValidator(("", "", ""), None))
    assert result["hangout"] == []
    assert result["grocery"] == []


def test_apply_corrections_relocates_via_validator():
    businesses = {"hangout": [_business("Havblik Agersø", street="Skolevangen 25", postal_code="4230", town="Magleby")]}
    rule = RelocateRule(name="Havblik Agersø", street="Agersø Møllevej 9A", postal_code="4244", town="Agersø By")
    validated = ValidatedAddress(street="Agersø Møllevej 9A", postal_code="4244", town="Agersø By", lat=55.21, lon=11.19)
    validator = _FakeValidator(("Agersø Møllevej 9A", "4244", "Agersø By"), validated)

    result = apply_corrections(businesses, BusinessCorrections(relocate=[rule]), validator)

    business = result["hangout"][0]
    assert business.street == "Agersø Møllevej 9A"
    assert business.postal_code == "4244"
    assert business.town == "Agersø By"
    assert business.lat == 55.21 and business.lon == 11.19


def test_apply_corrections_drops_relocate_whose_address_fails_validation():
    businesses = {"hangout": [_business("Havblik Agersø")]}
    rule = RelocateRule(name="Havblik Agersø", street="Nowhere Vej 1", postal_code="9999", town="Nergonby")
    validator = _FakeValidator(("Some Other Vej 1", "1000", "Elsewhere"), ValidatedAddress("x", "1000", "y", 0.0, 0.0))

    result = apply_corrections(businesses, BusinessCorrections(relocate=[rule]), validator)

    assert result["hangout"] == []


def test_apply_corrections_is_a_noop_when_no_rule_matches():
    businesses = {"hangout": [_business("Untouched Kro")]}
    corrections = BusinessCorrections(exclude=[ExcludeRule(name="Some Other Business")])
    result = apply_corrections(businesses, corrections, validator=_FakeValidator(("", "", ""), None))
    assert [b.name for b in result["hangout"]] == ["Untouched Kro"]
