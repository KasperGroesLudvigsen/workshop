"""One-shot real-data run: a live Boligsiden fetch (fritidsbolig only,
restricted to the postal ranges in config/thresholds.yaml) scored against
the real OSM geometry store, bathing-water data, and a real business
directory discovered via CVR, written out as a static site.

Boligsiden has no small per-query result cap the way Boliga did, so this
covers the whole configured region in one run rather than defaulting to a
narrow demo range.
"""
from __future__ import annotations

import argparse
import functools
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from screener.config import REPO_ROOT, Settings, load_settings
from screener.db import Database
from screener.fetch.bathing_water import build_badevand_lookup, load_badevand_sites
from screener.fetch.boligsiden import BoligsidenClient, normalize_case
from screener.fetch._rate_limit import RateLimiter
from screener.fetch.cvr_discovery import CvrPermanentClient
from screener.fetch.discovery_cache import load_or_build
from screener.fetch.flood_risk import fetch_flood_risk
from screener.fetch.listing_photo import fetch_listing_photo_url
from screener.fetch.osm_poi import extract_business_pois
from screener.fetch.page_fetch import fetch_html_page
from screener.fetch.web_search import TavilyClient
from screener.geo.business_directory import build_business_directory
from screener.geo.store import GeometryStore
from screener.resolve.address_regex import DatafordelerAddressValidator
from screener.resolve.business_corrections import apply_corrections, load_corrections
from screener.resolve.cvr_discovery import discover_and_resolve_all_categories
from screener.resolve.web_discovery_gaps import fill_gaps, find_gaps, prioritize_gaps
from screener.score.pipeline import HARD_FILTER_CVR_CATEGORIES, score_listings, sort_scored_listings
from screener.site.build import build_site

logger = logging.getLogger(__name__)


def _discover_business_directory(
    settings: Settings,
    db: Database,
    osm_pbf_path: str,
    listings: list[dict[str, Any]],
    *,
    osm_poi_cache_path: str,
    cvr_cache_path: str,
    business_corrections_path: str,
    rebuild_discovery_cache: bool = False,
) -> dict:
    cvr_client = CvrPermanentClient(db=db)
    validator = DatafordelerAddressValidator()

    # CVR bulk discovery + DAR validation and OSM POI extraction are both
    # region-wide and listing-independent -- neither needs re-running every
    # time this job picks up new listings, so both are cached to disk and
    # only rebuilt when actually stale. See fetch/discovery_cache.py.
    businesses_by_category = load_or_build(
        cvr_cache_path,
        max_age=timedelta(days=settings.cvr_cache_max_age_days),
        force_rebuild=rebuild_discovery_cache,
        build_fn=lambda: discover_and_resolve_all_categories(
            cvr_client, validator, settings.cvr_branch_codes, settings.postal_ranges,
            keyword_denylist_by_category=settings.cvr_category_keyword_denylist,
        ),
    )

    # M7 primary: OSM POIs -- already-placed real geometry, no address
    # validation needed, and it covers businesses CVR structurally can't
    # (e.g. run through a property-holding company registered elsewhere).
    osm_by_category = load_or_build(
        osm_poi_cache_path,
        source_path=Path(osm_pbf_path),
        force_rebuild=rebuild_discovery_cache,
        build_fn=lambda: extract_business_pois(osm_pbf_path),
    )
    for category, osm_businesses in osm_by_category.items():
        businesses_by_category.setdefault(category, []).extend(osm_businesses)

    # M7 fallback: Tavily web search, only for a (town, category) neither
    # CVR nor OSM covered -- see resolve/web_discovery_gaps.py. Optional
    # enrichment, not a required stage: any failure here (no API key, the
    # monthly credit budget exhausted, a real block from Tavily) is logged
    # and skipped rather than crashing the whole run.
    partial_directory = build_business_directory(businesses_by_category)
    gaps = prioritize_gaps(
        find_gaps(listings, partial_directory, settings.web_search_terms.keys(), settings.amenity_search_radius_km),
        HARD_FILTER_CVR_CATEGORIES,
    )
    logger.info("web_discovery_gaps: %d (town, category) gap(s) found after CVR+OSM", len(gaps))
    if gaps:
        try:
            tavily_client = TavilyClient(db=db, monthly_budget=settings.web_search_monthly_budget)
        except RuntimeError as e:
            logger.warning("web_discovery_gaps: skipping Tavily fallback (%s)", e)
        else:
            fetch_page = functools.partial(fetch_html_page, user_agent=settings.boligsiden.user_agent)
            web_results = fill_gaps(
                gaps,
                search_terms=settings.web_search_terms, client=tavily_client, validator=validator,
                fetch_page=fetch_page, max_calls_per_run=settings.web_search_max_calls_per_run,
            )
            for category, businesses in web_results.items():
                businesses_by_category.setdefault(category, []).extend(businesses)

    # Manual, hand-maintained fixes for businesses discovery got wrong (bad
    # address, wrong category) -- see config/business_corrections.yaml.
    # Applied last, after every source above is merged, so one entry fixes
    # a business regardless of which source found it.
    corrections = load_corrections(business_corrections_path)
    businesses_by_category = apply_corrections(businesses_by_category, corrections, validator)

    return build_business_directory(businesses_by_category)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--osm-store", default=str(REPO_ROOT / "data" / "osm_store.pkl"))
    parser.add_argument("--osm-pbf", default=str(REPO_ROOT / "data" / "denmark-latest.osm.pbf"))
    parser.add_argument("--badevand", default=str(REPO_ROOT / "data" / "badevand.geojson"))
    parser.add_argument("--out", default=str(REPO_ROOT / "data" / "site" / "index.html"))
    parser.add_argument("--osm-poi-cache", default=str(REPO_ROOT / "data" / "osm_poi_cache.pkl"))
    parser.add_argument("--cvr-cache", default=str(REPO_ROOT / "data" / "cvr_business_cache.pkl"))
    parser.add_argument(
        "--business-corrections", default=str(REPO_ROOT / "config" / "business_corrections.yaml"),
    )
    parser.add_argument(
        "--rebuild-discovery-cache", action="store_true",
        help="Force-rebuild the OSM POI and CVR discovery caches regardless of staleness.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = load_settings()
    if settings.fritidsbolig_address_type is None:
        raise SystemExit("fritidsbolig_address_type not set in config/thresholds.yaml")

    logger.info("loading real OSM geometry store from %s", args.osm_store)
    store = GeometryStore.load(args.osm_store)

    logger.info("loading real bathing-water sites from %s", args.badevand)
    badevand_lookup = build_badevand_lookup(load_badevand_sites(args.badevand))

    db = Database()
    client = BoligsidenClient(settings.boligsiden, db=db)
    logger.info(
        "fetching real Boligsiden listings for postal ranges %s, addressType=%r",
        settings.postal_ranges, settings.fritidsbolig_address_type,
    )
    raw_cases = list(
        client.fetch_postal_ranges(
            address_type=settings.fritidsbolig_address_type,
            postal_ranges=settings.postal_ranges,
        )
    )
    listings = [normalize_case(c) for c in raw_cases]
    logger.info("fetched %d real listings", len(listings))

    for listing in listings:
        listing["photo_url"] = fetch_listing_photo_url(
            listing["url"], user_agent=settings.boligsiden.user_agent, db=db
        )
    logger.info(
        "fetched preview photos for %d of %d listings",
        sum(1 for l in listings if l["photo_url"]), len(listings),
    )

    flood_rate_limiter = RateLimiter(5.0)
    for listing in listings:
        listing["flood_risk"] = fetch_flood_risk(
            listing["lon"], listing["lat"], user_agent=settings.boligsiden.user_agent,
            db=db, rate_limiter=flood_rate_limiter,
        )
    logger.info(
        "found a mapped flood risk for %d of %d listings",
        sum(1 for l in listings if l["flood_risk"]["current_return_period_years"] is not None), len(listings),
    )

    business_directory = _discover_business_directory(
        settings, db, args.osm_pbf, listings,
        osm_poi_cache_path=args.osm_poi_cache, cvr_cache_path=args.cvr_cache,
        business_corrections_path=args.business_corrections,
        rebuild_discovery_cache=args.rebuild_discovery_cache,
    )

    scored = score_listings(listings, store, settings, badevand_lookup=badevand_lookup, business_directory=business_directory)
    scored = sort_scored_listings(scored)
    passed = [r for r in scored if r["passed"]]
    logger.info("%d of %d listings pass all hard filters", len(passed), len(scored))

    out_path = build_site(scored, settings, args.out)
    logger.info("site written to %s", out_path)


if __name__ == "__main__":
    main()
