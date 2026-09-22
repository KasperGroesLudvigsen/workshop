from __future__ import annotations

from screener.fetch.listing_photo import extract_preview_image_url


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
