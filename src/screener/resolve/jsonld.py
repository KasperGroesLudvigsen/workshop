"""Step 2 of the address-resolution pipeline: JSON-LD on a business's own
site. Pure parsing, no model involved — a ``LocalBusiness``/``PostalAddress``
block, when present, is structured data the business published itself.

Danish food businesses are legally required to link to their Fødevarestyrelsen
findsmiley page (a "Smiley" report). Rather than treat that as a separate
crawl, :func:`find_findsmiley_link` pulls it out of the same page fetch, so
one HTTP call yields both an address candidate and the freshness signal
``fetch/findsmiley.py`` needs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_JSONLD_SCRIPT_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL
)
_FINDSMILEY_LINK_RE = re.compile(r'href=["\']([^"\']*findsmiley\.dk[^"\']*)["\']', re.IGNORECASE)


@dataclass
class JsonLdAddress:
    street_address: str | None
    postal_code: str | None
    address_locality: str | None
    name: str | None


def _walk_for_local_business(node: Any) -> dict[str, Any] | None:
    if isinstance(node, dict):
        types = node.get("@type")
        types = [types] if isinstance(types, str) else (types or [])
        if any(t.lower() in {"localbusiness", "restaurant", "cafeorcoffeeshop", "bar", "bakery", "grocerystore"} for t in types):
            return node
        for value in node.values():
            found = _walk_for_local_business(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_for_local_business(item)
            if found is not None:
                return found
    return None


def extract_local_business_address(html: str) -> JsonLdAddress | None:
    for match in _JSONLD_SCRIPT_RE.finditer(html):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        business = _walk_for_local_business(data)
        if business is None:
            continue
        address = business.get("address")
        if isinstance(address, dict):
            return JsonLdAddress(
                street_address=address.get("streetAddress"),
                postal_code=str(address["postalCode"]) if address.get("postalCode") else None,
                address_locality=address.get("addressLocality"),
                name=business.get("name"),
            )
    return None


def find_findsmiley_link(html: str) -> str | None:
    match = _FINDSMILEY_LINK_RE.search(html)
    return match.group(1) if match else None
