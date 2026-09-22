from __future__ import annotations

from screener.fetch.web_search import TavilyClient, TransportResponse, WebSearchResult
from screener.resolve.address_regex import AddressCandidate, ValidatedAddress
from screener.resolve.web_discovery import build_query, discover_via_web_search

WEB_SEARCH_TERMS = {"hangout": "restaurant café kro {town}"}


class _FakeValidator:
    def __init__(self, accept: tuple[str, str, str]):
        self._accept = accept

    def validate(self, candidate: AddressCandidate):
        if (candidate.street, candidate.postal_code, candidate.town) == self._accept:
            return ValidatedAddress(street=candidate.street, postal_code=candidate.postal_code, town=candidate.town, lat=55.1996, lon=11.4933)
        return None


def _client_with_results(results: list[WebSearchResult]) -> TavilyClient:
    def transport(url, body, api_key):
        return TransportResponse(
            200, "{}",
            {"results": [{"title": r.title, "url": r.url, "content": r.content} for r in results]},
        )
    return TavilyClient(api_key="tvly-test", transport=transport, requests_per_second=1000.0)


def test_build_query_substitutes_town():
    assert build_query("Bisserup", "hangout", WEB_SEARCH_TERMS) == "restaurant café kro Bisserup Danmark"


def test_jsonld_result_resolves_and_validates():
    client = _client_with_results([WebSearchResult(title="Bisserup Strand Kro | Facebook", url="https://kro.example", content="")])
    html = """
    <script type="application/ld+json">
    {"@type": "Restaurant", "name": "Bisserup Strand Kro",
     "address": {"streetAddress": "Havnevej 67", "postalCode": "4243", "addressLocality": "Rude"}}
    </script>
    """
    validator = _FakeValidator(("Havnevej 67", "4243", "Rude"))
    result = discover_via_web_search(
        "Bisserup", "hangout", search_terms=WEB_SEARCH_TERMS, client=client, validator=validator,
        expected_postal_code="4243", fetch_page=lambda url: html,
    )
    assert len(result) == 1
    assert result[0].name == "Bisserup Strand Kro"
    assert result[0].source_step == "web_search"
    assert result[0].lat == 55.1996


def test_falls_back_to_title_and_regex_when_no_jsonld():
    client = _client_with_results([WebSearchResult(title="Bisserup Is og Grillhus - Forside", url="https://grill.example", content="")])
    html = "<p>Besøg os på Havnevej 70, 4243 Rude for en is</p>"
    validator = _FakeValidator(("Havnevej 70", "4243", "Rude"))
    result = discover_via_web_search(
        "Bisserup", "hangout", search_terms=WEB_SEARCH_TERMS, client=client, validator=validator,
        expected_postal_code="4243", fetch_page=lambda url: html,
    )
    assert len(result) == 1
    assert result[0].name == "Bisserup Is og Grillhus"
    assert result[0].source_step == "web_search"


def test_falls_back_to_llm_extraction_as_last_resort():
    client = _client_with_results([WebSearchResult(title="Some Business", url="https://biz.example", content="")])
    html = "<p>no structured address and no regex-matching text here</p>"
    validator = _FakeValidator(("Havnevej 1", "4243", "Rude"))

    def fake_extractor(text: str) -> dict:
        return {"street": "Havnevej 1", "postal_code": "4243", "town": "Rude"}

    result = discover_via_web_search(
        "Bisserup", "hangout", search_terms=WEB_SEARCH_TERMS, client=client, validator=validator,
        expected_postal_code="4243", fetch_page=lambda url: html, llm_extractor=fake_extractor,
    )
    assert len(result) == 1
    assert result[0].source_step == "web_search"


def test_unvalidated_candidate_is_dropped():
    client = _client_with_results([WebSearchResult(title="Ghost Kro", url="https://ghost.example", content="")])
    html = "<p>Nowhere Vej 9, 9999 Nergonby</p>"
    validator = _FakeValidator(("Havnevej 1", "4243", "Rude"))

    def fake_extractor(text: str) -> dict:
        return {"street": None, "postal_code": None, "town": None}

    result = discover_via_web_search(
        "Bisserup", "hangout", search_terms=WEB_SEARCH_TERMS, client=client, validator=validator,
        expected_postal_code="4243", fetch_page=lambda url: html, llm_extractor=fake_extractor,
    )
    assert result == []


def test_page_fetch_failure_is_skipped_not_raised():
    client = _client_with_results([WebSearchResult(title="Unreachable", url="https://down.example", content="")])
    validator = _FakeValidator(("Havnevej 1", "4243", "Rude"))
    result = discover_via_web_search(
        "Bisserup", "hangout", search_terms=WEB_SEARCH_TERMS, client=client, validator=validator,
        expected_postal_code="4243", fetch_page=lambda url: None,
    )
    assert result == []
