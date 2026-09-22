"""Turns raw cvr-permanent business records into validated
:class:`~screener.resolve.pipeline.ResolvedBusiness` records — the bulk
counterpart to ``resolve/cvr_match.py`` (which matches one already-known
*name* to a CVR record). This module instead starts from CVR's own bulk
enumeration (``fetch/cvr_discovery.py``) and turns *that* into resolved
businesses.

Still goes through the same real address-register gate as everything else
in this codebase: CVR being the authoritative source for company data
doesn't make its address trustworthy on its own (registered addresses are
sometimes an accountant's office or a virtual-office service, not the
actual shopfront) — see ``resolve/pipeline.py``'s module docstring for the
"never geocode raw text" rule this is following. A hit that fails
validation is logged and dropped, not raised — that's an expected, not
exceptional, outcome for a subset of any real batch.
"""
from __future__ import annotations

import logging
from typing import Any

from screener.fetch.cvr_discovery import CvrPermanentClient
from screener.resolve.address_regex import AddressCandidate, AddressValidator
from screener.resolve.pipeline import ResolvedBusiness

logger = logging.getLogger(__name__)


def _candidate_from_hit(raw: dict[str, Any]) -> AddressCandidate | None:
    addr = raw.get("virksomhedMetadata", {}).get("nyesteBeliggenhedsadresse") or {}
    road = addr.get("vejnavn")
    house_number = addr.get("husnummerFra")
    postal_code = addr.get("postnummer")
    town = addr.get("postdistrikt")
    if not (road and house_number and postal_code and town):
        return None
    return AddressCandidate(
        street=f"{road} {house_number}",
        postal_code=str(postal_code),
        town=town,
        raw_text="cvr_discovery",
    )


def resolve_discovered_businesses(
    hits: list[dict[str, Any]], validator: AddressValidator, category: str,
    keyword_denylist: list[str] | None = None,
) -> list[ResolvedBusiness]:
    keyword_denylist = keyword_denylist or []
    resolved: list[ResolvedBusiness] = []
    for raw in hits:
        name = raw.get("virksomhedMetadata", {}).get("nyesteNavn", {}).get("navn")
        candidate = _candidate_from_hit(raw)
        if name is None or candidate is None:
            logger.info("cvr_discovery: skipping %s (missing name or address fields)", raw.get("cvrNummer"))
            continue
        if any(keyword.lower() in name.lower() for keyword in keyword_denylist):
            logger.info("cvr_discovery: skipping %r (name matches %s keyword denylist)", name, category)
            continue
        validated = validator.validate(candidate)
        if validated is None:
            logger.info("cvr_discovery: %r (cvr %s) failed address-register validation, dropping", name, raw.get("cvrNummer"))
            continue
        resolved.append(
            ResolvedBusiness(
                name=name, street=validated.street, postal_code=validated.postal_code,
                town=validated.town, lat=validated.lat, lon=validated.lon,
                source_step="cvr_discovery",
            )
        )
    logger.info("cvr_discovery: resolved %d/%d %s candidates", len(resolved), len(hits), category)
    return resolved


def discover_and_resolve_all_categories(
    cvr_client: CvrPermanentClient,
    validator: AddressValidator,
    branch_codes_by_category: dict[str, list[str]],
    postal_ranges: list[tuple[int, int]],
    keyword_denylist_by_category: dict[str, list[str]] | None = None,
) -> dict[str, list[ResolvedBusiness]]:
    """Discover + resolve every CVR-backed category in one pass -- the
    ``build_fn`` handed to ``fetch.discovery_cache.load_or_build`` so the
    whole (Elasticsearch queries + rate-limited DAR validation) pass can be
    skipped entirely on a cache hit, not just individual HTTP calls."""
    keyword_denylist_by_category = keyword_denylist_by_category or {}
    businesses_by_category: dict[str, list[ResolvedBusiness]] = {}
    for category, branch_codes in branch_codes_by_category.items():
        logger.info("discovering %s businesses (branch codes %s)", category, branch_codes)
        hits = list(
            cvr_client.discover_active_businesses(
                branch_code_prefixes=branch_codes, postal_ranges=postal_ranges,
            )
        )
        resolved = resolve_discovered_businesses(
            hits, validator, category=category, keyword_denylist=keyword_denylist_by_category.get(category),
        )
        logger.info("%s: %d candidates, %d validated", category, len(hits), len(resolved))
        businesses_by_category[category] = resolved
    return businesses_by_category
