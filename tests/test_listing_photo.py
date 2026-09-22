from __future__ import annotations

from screener.db import Database
from screener.fetch.listing_photo import extract_meta_refresh_url, extract_preview_image_url, fetch_listing_photo_url


def test_extracts_og_image_property_before_content():
    html = '<head><meta property="og:image" content="https://example.com/house.jpg"></head>'
    assert extract_preview_image_url(html) == "https://example.com/house.jpg"


def test_extracts_og_image_content_before_property():
    html = '<head><meta content="https://example.com/house.jpg" property="og:image"></head>'
    assert extract_preview_image_url(html) == "https://example.com/house.jpg"


def test_falls_back_to_twitter_image():
    html = '<head><meta name="twitter:image" content="https://example.com/house2.jpg"></head>'
    assert extract_preview_image_url(html) == "https://example.com/house2.jpg"


def test_returns_none_when_no_preview_image_tag():
    html = "<head><title>No image here</title></head>"
    assert extract_preview_image_url(html) is None


def test_extract_meta_refresh_url_unescapes_html_entities():
    html = '<meta id="__next-page-redirect" http-equiv="refresh" content="1;url=https://www.danbolig.dk/?propertyid=1&amp;brokerid=244">'
    assert extract_meta_refresh_url(html) == "https://www.danbolig.dk/?propertyid=1&brokerid=244"


def test_extract_meta_refresh_url_returns_none_when_absent():
    assert extract_meta_refresh_url("<html><body>hello</body></html>") is None


def test_fetch_listing_photo_url_follows_meta_refresh_to_find_the_image(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.duckdb")
    stub_html = '<meta http-equiv="refresh" content="1;url=https://agent.example.com/house-1">'
    agent_html = '<meta property="og:image" content="https://agent.example.com/photo.jpg">'
    pages = {
        "https://boligsiden.dk/viderestilling/abc": stub_html,
        "https://agent.example.com/house-1": agent_html,
    }
    fetch_calls = []

    def fake_fetch(url, *, user_agent):
        fetch_calls.append(url)
        return pages.get(url)

    monkeypatch.setattr("screener.fetch.listing_photo.fetch_html_page", fake_fetch)

    photo_url = fetch_listing_photo_url("https://boligsiden.dk/viderestilling/abc", user_agent="test", db=db)

    assert photo_url == "https://agent.example.com/photo.jpg"
    assert fetch_calls == ["https://boligsiden.dk/viderestilling/abc", "https://agent.example.com/house-1"]
    db.close()
