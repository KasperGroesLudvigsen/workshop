from __future__ import annotations

import pytest

from screener.db import Database
from screener.fetch.web_search import TavilyBlockedError, TavilyBudgetExceededError, TavilyClient, TransportResponse


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


def test_cache_hit_skips_transport_and_does_not_rewrite_row(tmp_path):
    db = Database(tmp_path / "test.duckdb")
    calls = []

    def transport(url, body, api_key):
        calls.append(body)
        return TransportResponse(
            200, '{"results": [{"title": "Bisserup Strand Kro", "url": "https://example.dk", "content": "x"}]}',
            {"results": [{"title": "Bisserup Strand Kro", "url": "https://example.dk", "content": "x"}]},
        )

    client = _client(transport, db=db)
    first = client.search("Bisserup Strand Kro")
    assert len(calls) == 1
    rows_after_first = list(db.iter_raw_responses("tavily_search"))
    assert len(rows_after_first) == 1

    second = client.search("Bisserup Strand Kro")
    assert len(calls) == 1  # no new transport call
    assert second == first
    rows_after_second = list(db.iter_raw_responses("tavily_search"))
    assert rows_after_second == rows_after_first  # row not rewritten, no new row
    assert client.calls_made == 1  # cache hit never increments calls_made


def test_cached_blocked_response_replays_as_blocked(tmp_path):
    db = Database(tmp_path / "test.duckdb")

    def transport(url, body, api_key):
        return TransportResponse(401, "unauthorized", None)

    client = _client(transport, db=db)
    with pytest.raises(TavilyBlockedError):
        client.search("query")

    # Second call is a cache hit -- must replay the same error, not silently
    # look like success.
    with pytest.raises(TavilyBlockedError):
        client.search("query")


def test_monthly_budget_exceeded_blocks_new_call_without_touching_transport(tmp_path):
    db = Database(tmp_path / "test.duckdb")
    for i in range(3):
        db.save_raw_response(
            source="tavily_search", url="https://api.tavily.com/search",
            params={"query": f"already searched {i}"}, status_code=200, body="{}",
        )

    calls = []

    def transport(url, body, api_key):
        calls.append(body)
        return TransportResponse(200, "{}", {"results": []})

    client = _client(transport, db=db, monthly_budget=3)
    with pytest.raises(TavilyBudgetExceededError):
        client.search("a brand new query")
    assert calls == []
    assert client.calls_made == 0


def test_calls_made_only_counts_real_network_calls(tmp_path):
    db = Database(tmp_path / "test.duckdb")

    def transport(url, body, api_key):
        return TransportResponse(200, "{}", {"results": []})

    client = _client(transport, db=db)
    assert client.calls_made == 0
    client.search("query one")
    assert client.calls_made == 1
    client.search("query one")  # cache hit
    assert client.calls_made == 1
    client.search("query two")
    assert client.calls_made == 2
