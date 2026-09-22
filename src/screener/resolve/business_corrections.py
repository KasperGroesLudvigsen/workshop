"""Hand-maintained fixes for businesses the discovery pipeline resolved
wrong -- a wrong (but real, DAR-valid) address, or a category it doesn't
actually belong to. See ``config/business_corrections.yaml``.

Matching is by name alone, on purpose: it's the one identifier a person can
copy straight off the site (a popup or table row) with no extra lookup,
since ``geo/amenities.py::category_summary`` ships ``ResolvedBusiness.name``
to the page verbatim. Applied once, after every discovery source (CVR, OSM
POI, Tavily web-search) has been merged, so one entry fixes a business no
matter which source found it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from screener.resolve.address_regex import AddressCandidate, AddressValidator
from screener.resolve.pipeline import ResolvedBusiness

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExcludeRule:
    name: str
    reason: str | None = None


@dataclass(frozen=True)
class RelocateRule:
    name: str
    street: str
    postal_code: str
    town: str
    reason: str | None = None


@dataclass(frozen=True)
class BusinessCorrections:
    exclude: list[ExcludeRule] = field(default_factory=list)
    relocate: list[RelocateRule] = field(default_factory=list)


def _normalize(name: str) -> str:
    return " ".join(name.split()).casefold()


def load_corrections(path: Path | str) -> BusinessCorrections:
    path = Path(path)
    if not path.exists():
        return BusinessCorrections()
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return BusinessCorrections(
        exclude=[ExcludeRule(**item) for item in raw.get("exclude", [])],
        relocate=[RelocateRule(**item) for item in raw.get("relocate", [])],
    )


def apply_corrections(
    businesses_by_category: dict[str, list[ResolvedBusiness]],
    corrections: BusinessCorrections,
    validator: AddressValidator,
) -> dict[str, list[ResolvedBusiness]]:
    exclude_names = {_normalize(rule.name) for rule in corrections.exclude}
    relocate_by_name = {_normalize(rule.name): rule for rule in corrections.relocate}
    matched: set[str] = set()

    result: dict[str, list[ResolvedBusiness]] = {}
    for category, businesses in businesses_by_category.items():
        kept: list[ResolvedBusiness] = []
        for business in businesses:
            key = _normalize(business.name)

            if key in exclude_names:
                matched.add(key)
                continue

            rule = relocate_by_name.get(key)
            if rule is not None:
                matched.add(key)
                validated = validator.validate(
                    AddressCandidate(street=rule.street, postal_code=rule.postal_code, town=rule.town, raw_text="business_corrections")
                )
                if validated is None:
                    logger.warning(
                        "business_corrections: relocate rule for %r has an address that failed validation "
                        "(%s, %s %s) -- dropping the business instead of leaving it at the old location",
                        rule.name, rule.street, rule.postal_code, rule.town,
                    )
                    continue
                business = replace(
                    business, street=validated.street, postal_code=validated.postal_code,
                    town=validated.town, lat=validated.lat, lon=validated.lon,
                )

            kept.append(business)
        result[category] = kept

    for key, name in [(_normalize(r.name), r.name) for r in corrections.exclude] + [(_normalize(r.name), r.name) for r in corrections.relocate]:
        if key not in matched:
            logger.warning("business_corrections: rule for %r matched no business in this run -- stale entry or typo?", name)

    return result
