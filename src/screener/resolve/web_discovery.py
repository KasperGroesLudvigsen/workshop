"""Tertiary business discovery for M7: a generic web search, only reached
for a town/category that both CVR discovery (``resolve/cvr_discovery.py``)
and OSM POI extraction (``fetch/osm_poi.py``) missed.

Unlike OSM POIs — already-placed real geometry, trusted with no address
gate — a search result's page really is raw text that could be wrong, so
every candidate here goes through the same three-step extraction cascade
as ``resolve/pipeline.py``'s per-business path (JSON-LD, then regex, then
LLM, each gated by the same ``AddressValidator``), just applied to a
search result's page instead of an already-known business's own site.
"""
from __future__ import annotations

import logging
import re

from screener.resolve import jsonld, llm_extract
from screener.resolve.address_regex import AddressCandidate, AddressValidator, resolve_first_valid
from screener.resolve.pipeline import PageFetcher, ResolvedBusiness
from screener.fetch.web_search import TavilyClient

logger = logging.getLogger(__name__)

_TITLE_SUFFIX_RE = re.compile(r"\s*[|\-–—]\s*(Facebook|Instagram|Hjemmeside|Forside).*$", re.IGNORECASE)


def _clean_title(title: str) -> str:
    return _TITLE_SUFFIX_RE.sub("", title).strip()


def build_query(town: str, category: str, search_terms: dict[str, str]) -> str:
    template = search_terms[category]
    return template.format(town=town)


def _resolve_result(
    title: str,
    html: str,
    validator: AddressValidator,
    expected_postal_code: str,
    llm_extractor: llm_extract.Extractor | None,
) -> ResolvedBusiness | None:
    name = _clean_title(title)

    ld_address = jsonld.extract_local_business_address(html)
    if ld_address and ld_address.street_address and ld_address.postal_code and ld_address.address_locality:
        candidate = AddressCandidate(
            street=ld_address.street_address,
            postal_code=ld_address.postal_code,
            town=ld_address.address_locality,
            raw_text="jsonld",
        )
        validated = validator.validate(candidate)
        if validated is not None:
            return ResolvedBusiness(
                name=ld_address.name or name, street=validated.street, postal_code=validated.postal_code,
                town=validated.town, lat=validated.lat, lon=validated.lon, source_step="web_search",
            )

    validated = resolve_first_valid(html, validator, expected_postal_code=expected_postal_code)
    if validated is not None:
        return ResolvedBusiness(
            name=name, street=validated.street, postal_code=validated.postal_code,
            town=validated.town, lat=validated.lat, lon=validated.lon, source_step="web_search",
        )

    extractor = llm_extractor or llm_extract.anthropic_extractor
    candidate = llm_extract.extract_candidate(html, extractor=extractor)
    if candidate is not None:
        validated = validator.validate(candidate)
        if validated is not None:
            return ResolvedBusiness(
                name=name, street=validated.street, postal_code=validated.postal_code,
                town=validated.town, lat=validated.lat, lon=validated.lon, source_step="web_search",
            )

    return None


def discover_via_web_search(
    town: str,
    category: str,
    *,
    search_terms: dict[str, str],
    client: TavilyClient,
    validator: AddressValidator,
    expected_postal_code: str,
    fetch_page: PageFetcher,
    llm_extractor: llm_extract.Extractor | None = None,
    max_results: int = 5,
) -> list[ResolvedBusiness]:
    query = build_query(town, category, search_terms)
    results = client.search(query, max_results=max_results)
    resolved: list[ResolvedBusiness] = []
    for result in results:
        html = fetch_page(result.url)
        if not html:
            continue
        business = _resolve_result(result.title, html, validator, expected_postal_code, llm_extractor)
        if business is not None:
            resolved.append(business)
    logger.info("web_discovery: resolved %d/%d %s candidate(s) for %r", len(resolved), len(results), category, town)
    return resolved
