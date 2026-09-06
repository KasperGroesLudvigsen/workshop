from __future__ import annotations

from screener.fetch.cvr import CvrClient, TransportResponse
from screener.resolve.address_regex import AddressCandidate, ValidatedAddress
from screener.resolve.pipeline import resolve_business_address

VALID_ADDR = ("Havnevej 1", "4243", "Rude")


class FakeValidator:
    """Only ever validates VALID_ADDR — everything else (including anything
    an LLM might hallucinate) is rejected, simulating the real address
    register gate."""

    def __init__(self, valid=VALID_ADDR):
        self._valid = valid
        self.calls: list[AddressCandidate] = []

    def validate(self, candidate: AddressCandidate):
        self.calls.append(candidate)
        if (candidate.street, candidate.postal_code, candidate.town) == self._valid:
            return ValidatedAddress(street=candidate.street, postal_code=candidate.postal_code,
                                     town=candidate.town, lat=55.4, lon=11.5)
        return None


def _cvr_client(body, status=200):
    return CvrClient(transport=lambda url, params, headers: TransportResponse(status, body, "{}"))


def _no_cvr_match_client():
    # cvrapi.dk returns an unrelated / low-confidence company -> step 1 fails.
    body = {"vat": "9", "name": "Something Else Entirely", "address": "Other Vej 9",
            "zipcode": 9999, "city": "Elsewhere", "enddate": None}
    return _cvr_client(body)


def test_step1_cvr_match_succeeds():
    body = {"vat": "1", "name": "Bisserup Havnekro", "address": "Havnevej 1",
            "zipcode": 4243, "city": "Rude", "enddate": None}
    client = _cvr_client(body)
    validator = FakeValidator()
    result = resolve_business_address(
        name="Bisserup Havnekro", postal_code=4243, cvr_client=client, validator=validator,
    )
    assert result is not None
    assert result.source_step == "cvr_match"
    assert (result.lat, result.lon) == (55.4, 11.5)


def test_falls_through_to_jsonld_when_cvr_fails():
    html = """
    <script type="application/ld+json">
    {"@type": "Restaurant", "name": "Bisserup Havnekro",
     "address": {"streetAddress": "Havnevej 1", "postalCode": "4243", "addressLocality": "Rude"}}
    </script>"""
    result = resolve_business_address(
        name="Bisserup Havnekro", postal_code=4243,
        cvr_client=_no_cvr_match_client(), validator=FakeValidator(), website_html=html,
    )
    assert result is not None
    assert result.source_step == "jsonld"


def test_falls_through_to_regex_when_cvr_and_jsonld_fail():
    html = "<html><body>Besoeg os pa Havnevej 1, 4243 Rude!</body></html>"
    result = resolve_business_address(
        name="Bisserup Havnekro", postal_code=4243,
        cvr_client=_no_cvr_match_client(), validator=FakeValidator(), website_html=html,
    )
    assert result is not None
    assert result.source_step == "address_regex"


def test_falls_through_to_llm_when_all_else_fails():
    html = "<html><body>No structured info, just prose about the kro near the harbour.</body></html>"

    def fake_extractor(text: str) -> dict:
        return {"street": "Havnevej 1", "postal_code": "4243", "town": "Rude"}

    result = resolve_business_address(
        name="Bisserup Havnekro", postal_code=4243,
        cvr_client=_no_cvr_match_client(), validator=FakeValidator(), website_html=html,
        llm_extractor=fake_extractor,
    )
    assert result is not None
    assert result.source_step == "llm_extract"


def test_hallucinated_llm_address_is_dropped_not_geocoded():
    """The core non-negotiable gate: an LLM candidate that doesn't validate
    must drop the row, never be trusted directly."""
    html = "<html><body>Vague prose, no real address here.</body></html>"

    def hallucinating_extractor(text: str) -> dict:
        return {"street": "Fictional Vej 42", "postal_code": "0000", "town": "Nowhereville"}

    validator = FakeValidator()
    result = resolve_business_address(
        name="Ghost Kro", postal_code=4243,
        cvr_client=_no_cvr_match_client(), validator=validator, website_html=html,
        llm_extractor=hallucinating_extractor,
    )
    assert result is None
    # the hallucinated candidate really was offered to the validator and rejected,
    # not silently skipped
    assert any(c.street == "Fictional Vej 42" for c in validator.calls)


def test_total_failure_returns_none_and_does_not_raise():
    result = resolve_business_address(
        name="Nowhere Kro", postal_code=4243,
        cvr_client=_no_cvr_match_client(), validator=FakeValidator(),
        website_html="<html><body>nothing useful here</body></html>",
        llm_extractor=lambda text: {"street": None, "postal_code": None, "town": None},
    )
    assert result is None
