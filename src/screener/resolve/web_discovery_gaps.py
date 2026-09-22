"""Decides which (town, category) pairs are worth a Tavily web search --
and, since Tavily's free tier is a hard 1,000 credits/month, enforces the
budget that makes running this repeatedly (or at full region scale) safe.

Two independent limits, not one, because they answer different questions:
- ``monthly_budget`` (enforced inside ``TavilyClient`` itself, see
  ``fetch/web_search.py``) is the hard "never exceed the real quota"
  ceiling, tracked in the persistent ``raw_responses`` cache across every
  run this calendar month.
- ``max_calls_per_run`` here is a per-run pacing cap so one large run
  (e.g. the first cold-cache run across the whole region) can't spend the
  entire month's budget by itself, leaving nothing for later re-runs.

Gap detection reuses the exact same nearest-distance machinery scoring
uses (``geo.amenities.category_summary``), so "does this town already have
a candidate" means the same thing here as it does when a listing is
actually scored.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

from shapely.geometry import Point

from screener.fetch.web_search import TavilyBlockedError, TavilyBudgetExceededError, TavilyClient
from screener.geo.amenities import category_summary
from screener.geo.projection import point_to_xy
from screener.geo.store import Layer
from screener.resolve.address_regex import AddressValidator
from screener.resolve.pipeline import PageFetcher, ResolvedBusiness
from screener.resolve.web_discovery import discover_via_web_search
from screener.resolve import llm_extract

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Gap:
    town: str
    category: str
    zip_code: str


def find_gaps(
    listings: list[dict[str, Any]],
    directory: dict[str, Layer],
    categories: Iterable[str],
    gap_radius_km: float,
) -> list[Gap]:
    """One :class:`Gap` per (town, category) where no listing in that town
    has a ``directory`` candidate for that category within
    ``gap_radius_km`` -- including categories entirely missing from
    ``directory`` (e.g. ``ice_cream``, which has no CVR branch code at
    all)."""
    radius_m = gap_radius_km * 1000.0
    categories = list(categories)
    seen: set[tuple[str, str]] = set()
    gaps: list[Gap] = []
    for listing in listings:
        town = listing.get("town")
        if not town:
            continue
        point = Point(*point_to_xy(listing["lon"], listing["lat"]))
        for category in categories:
            key = (town, category)
            if key in seen:
                continue
            layer = directory.get(category)
            nearest_km = category_summary(layer, point, radius_m)["nearest_km"] if layer is not None else None
            if nearest_km is None or nearest_km > gap_radius_km:
                seen.add(key)
                gaps.append(Gap(town=town, category=category, zip_code=str(listing.get("zip_code") or "")))
    return gaps


def prioritize_gaps(gaps: list[Gap], hard_filter_categories: tuple[str, ...]) -> list[Gap]:
    """Hard-filter category gaps (hangout/grocery -- affect pass/fail)
    first, informational ones after, so a budget/run cap hit partway
    through spends its calls on the categories that matter most."""
    return sorted(gaps, key=lambda g: g.category not in hard_filter_categories)


def fill_gaps(
    gaps: list[Gap],
    *,
    search_terms: dict[str, str],
    client: TavilyClient,
    validator: AddressValidator,
    fetch_page: PageFetcher,
    max_calls_per_run: int,
    llm_extractor: llm_extract.Extractor | None = None,
) -> dict[str, list[ResolvedBusiness]]:
    results: dict[str, list[ResolvedBusiness]] = {}
    calls_this_run = 0
    for i, gap in enumerate(gaps):
        if calls_this_run >= max_calls_per_run:
            logger.warning(
                "web_discovery_gaps: hit max_calls_per_run=%d, skipping %d remaining gap(s)",
                max_calls_per_run, len(gaps) - i,
            )
            break
        calls_before = client.calls_made
        try:
            resolved = discover_via_web_search(
                gap.town, gap.category,
                search_terms=search_terms, client=client, validator=validator,
                expected_postal_code=gap.zip_code, fetch_page=fetch_page, llm_extractor=llm_extractor,
            )
        except TavilyBudgetExceededError as e:
            logger.warning("web_discovery_gaps: monthly Tavily budget exhausted (%s), stopping gap-fill", e)
            break
        except TavilyBlockedError as e:
            logger.warning("web_discovery_gaps: Tavily blocked (%s), stopping gap-fill", e)
            break
        if client.calls_made > calls_before:
            calls_this_run += 1
        if resolved:
            results.setdefault(gap.category, []).extend(resolved)
    logger.info(
        "web_discovery_gaps: %d gap(s), %d real Tavily call(s) made, %d resolved business(es)",
        len(gaps), calls_this_run, sum(len(v) for v in results.values()),
    )
    return results
