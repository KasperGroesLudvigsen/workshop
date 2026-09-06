"""Business name -> validated address.

A fixed pipeline, not an agent loop: four narrow steps tried in order,
cheapest first, stopping at the first address that validates against the
Danish address register. Non-determinism (an LLM call, a fuzzy match) never
reaches a result directly — every candidate, regardless of which step
produced it, passes through the same ``AddressValidator`` gate. Whatever
survives none of the four steps is dropped with a logged reason. This is
the "never geocode raw LLM output" rule the brief calls non-negotiable,
applied uniformly rather than as a special case for step 4.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from screener.fetch.cvr import CvrClient
from screener.resolve import cvr_match, jsonld, llm_extract
from screener.resolve.address_regex import AddressCandidate, AddressValidator, resolve_first_valid

logger = logging.getLogger(__name__)

PageFetcher = Callable[[str], str | None]


@dataclass
class ResolvedBusiness:
    name: str
    street: str
    postal_code: str
    town: str
    lat: float
    lon: float
    source_step: str  # "cvr_match" | "jsonld" | "address_regex" | "llm_extract"


def _address_candidate_from_cvr(company) -> AddressCandidate:
    return AddressCandidate(
        street=company.address or "", postal_code=company.zip_code or "", town=company.city or "", raw_text="cvr"
    )


def _to_resolved(name: str, validated, step: str) -> ResolvedBusiness:
    return ResolvedBusiness(
        name=name,
        street=validated.street,
        postal_code=validated.postal_code,
        town=validated.town,
        lat=validated.lat,
        lon=validated.lon,
        source_step=step,
    )


def resolve_business_address(
    *,
    name: str,
    postal_code: int,
    cvr_client: CvrClient,
    validator: AddressValidator,
    website_url: str | None = None,
    website_html: str | None = None,
    fetch_page: PageFetcher | None = None,
    llm_extractor: llm_extract.Extractor | None = None,
) -> ResolvedBusiness | None:
    # Step 1: CVR fuzzy match — cheapest, highest yield.
    match = cvr_match.match_name_to_cvr(cvr_client, name, postal_code)
    if match is not None and match.company.address and match.company.zip_code:
        validated = validator.validate(_address_candidate_from_cvr(match.company))
        if validated is not None:
            return _to_resolved(name, validated, "cvr_match")
        logger.info("cvr_match found %r but its address failed register validation", name)

    html = website_html
    if html is None and website_url is not None and fetch_page is not None:
        html = fetch_page(website_url)

    if html:
        # Step 2: JSON-LD published by the business's own site.
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
                return _to_resolved(name, validated, "jsonld")

        # Step 3: regex candidates from the raw page text.
        validated = resolve_first_valid(html, validator, expected_postal_code=str(postal_code))
        if validated is not None:
            return _to_resolved(name, validated, "address_regex")

        # Step 4: LLM extraction, last resort — still gated by `validator`.
        extractor = llm_extractor or llm_extract.anthropic_extractor
        candidate = llm_extract.extract_candidate(html, extractor=extractor)
        if candidate is not None:
            validated = validator.validate(candidate)
            if validated is not None:
                return _to_resolved(name, validated, "llm_extract")

    logger.warning("could not resolve a validated address for %r in postal %s — dropping", name, postal_code)
    return None
