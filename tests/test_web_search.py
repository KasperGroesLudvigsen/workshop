from __future__ import annotations

import pytest

from screener.fetch.web_search import TavilyBlockedError, TavilyClient, TransportResponse


def _client(transport, **kwargs) -> TavilyClient:
    return TavilyClient(api_key="tvly-test", transport=transport, requests_per_second=1000.0, **kwargs)


def test_search_sends_bearer_auth_and_query_body():
    captured = []

    def transport(url, body, api_key):
        captured.append((url, body, api_key))
        return TransportResponse(200, "{}", {"results": []})

    client = _client(transport)
    client.search("Bisserup Strand Kro")

    assert len(captured) == 1
    url, body, api_key = captured[0]
    assert url == "https://api.tavily.com/search"
    assert body == {"query": "Bisserup Strand Kro", "max_results": 5}
    assert api_key == "tvly-test"


def test_search_returns_title_url_content():
    def transport(url, body, api_key):
        return TransportResponse(
            200, "{}",
            {"results": [{"title": "Bisserup Strand Kro", "url": "https://example.dk", "content": "Havnevej 67, 4243 Rude"}]},
        )

    client = _client(transport)
    results = client.search("Bisserup Strand Kro")
    assert len(results) == 1
    assert results[0].title == "Bisserup Strand Kro"
    assert results[0].url == "https://example.dk"
    assert results[0].content == "Havnevej 67, 4243 Rude"


def test_blocked_status_raises_loudly():
    def transport(url, body, api_key):
        return TransportResponse(401, "unauthorized", None)

    client = _client(transport)
    with pytest.raises(TavilyBlockedError):
        client.search("query")


def test_missing_api_key_raises():
    import os

    old = os.environ.pop("TAVILY_API_KEY", None)
    try:
        with pytest.raises(RuntimeError):
            TavilyClient(transport=lambda *a: None)
    finally:
        if old is not None:
            os.environ["TAVILY_API_KEY"] = old
