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
import logging

from screener.config import REPO_ROOT, Settings, load_settings
from screener.db import Database
from screener.fetch.bathing_water import build_badevand_lookup, load_badevand_sites
from screener.fetch.boligsiden import BoligsidenClient, normalize_case
from screener.fetch.cvr_discovery import CvrPermanentClient
from screener.geo.business_directory import build_business_directory
from screener.geo.store import GeometryStore
from screener.resolve.address_regex import DatafordelerAddressValidator
from screener.resolve.cvr_discovery import resolve_discovered_businesses
from screener.score.pipeline import score_listings, sort_scored_listings
from screener.site.build import build_site

logger = logging.getLogger(__name__)


def _discover_business_directory(settings: Settings, db: Database) -> dict:
    cvr_client = CvrPermanentClient(db=db)
    validator = DatafordelerAddressValidator()
    businesses_by_category = {}
    for category, branch_codes in settings.cvr_branch_codes.items():
        logger.info("discovering %s businesses (branch codes %s)", category, branch_codes)
        hits = list(
            cvr_client.discover_active_businesses(
                branch_code_prefixes=branch_codes, postal_ranges=settings.postal_ranges,
            )
        )
        resolved = resolve_discovered_businesses(hits, validator, category=category)
        logger.info("%s: %d candidates, %d validated", category, len(hits), len(resolved))
        businesses_by_category[category] = resolved
    return build_business_directory(businesses_by_category)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--osm-store", default=str(REPO_ROOT / "data" / "osm_store.pkl"))
    parser.add_argument("--badevand", default=str(REPO_ROOT / "data" / "badevand.geojson"))
    parser.add_argument("--out", default=str(REPO_ROOT / "data" / "site" / "index.html"))
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

    business_directory = _discover_business_directory(settings, db)

    scored = score_listings(listings, store, settings, badevand_lookup=badevand_lookup, business_directory=business_directory)
    scored = sort_scored_listings(scored)
    passed = [r for r in scored if r["passed"]]
    logger.info("%d of %d listings pass all hard filters", len(passed), len(scored))

    out_path = build_site(scored, settings, args.out)
    logger.info("site written to %s", out_path)


if __name__ == "__main__":
    main()
