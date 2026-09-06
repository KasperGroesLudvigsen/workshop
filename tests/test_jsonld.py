from __future__ import annotations

from screener.resolve.jsonld import extract_local_business_address, find_findsmiley_link

HTML_WITH_JSONLD = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "CafeOrCoffeeShop",
  "name": "Bisserup Havnekro",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "Havnevej 1",
    "postalCode": "4243",
    "addressLocality": "Rude"
  }
}
</script>
</head>
<body>
  <footer>Se vores <a href="https://www.findsmiley.dk/1234">smiley-rapport</a></footer>
</body></html>
"""

HTML_WITHOUT_JSONLD = "<html><body><p>Welcome to our restaurant!</p></body></html>"

HTML_WITH_GRAPH_WRAPPER = """
<script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [
  {"@type": "WebSite", "name": "irrelevant"},
  {"@type": "Restaurant", "name": "Skovkroen", "address": {"@type": "PostalAddress", "streetAddress": "Skovvej 3", "postalCode": "4200", "addressLocality": "Slagelse"}}
]}
</script>
"""


def test_extract_local_business_address_finds_address():
    result = extract_local_business_address(HTML_WITH_JSONLD)
    assert result is not None
    assert result.street_address == "Havnevej 1"
    assert result.postal_code == "4243"
    assert result.address_locality == "Rude"
    assert result.name == "Bisserup Havnekro"


def test_extract_local_business_address_returns_none_when_absent():
    assert extract_local_business_address(HTML_WITHOUT_JSONLD) is None


def test_extract_local_business_address_handles_graph_wrapper():
    result = extract_local_business_address(HTML_WITH_GRAPH_WRAPPER)
    assert result is not None
    assert result.name == "Skovkroen"
    assert result.postal_code == "4200"


def test_find_findsmiley_link():
    assert find_findsmiley_link(HTML_WITH_JSONLD) == "https://www.findsmiley.dk/1234"
    assert find_findsmiley_link(HTML_WITHOUT_JSONLD) is None
