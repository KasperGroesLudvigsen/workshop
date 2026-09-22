"""Typed access to config/thresholds.yaml — the single source of truth for
hard-filter thresholds, lake eligibility switches, and region scope.

Both the batch scorer and the site builder load settings through this module
so a threshold change in one YAML file propagates everywhere without code
edits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "thresholds.yaml"


@dataclass(frozen=True)
class BoligsidenSettings:
    base_url: str
    requests_per_second: float
    per_page: int
    user_agent: str


@dataclass(frozen=True)
class Settings:
    water_km: float
    hangout_km: float
    grocery_km: float
    min_lake_area_ha: float
    lake_strict: bool
    amenity_search_radius_km: float
    postal_ranges: list[tuple[int, int]]
    fritidsbolig_address_type: str | None
    hard_filter_categories: list[str]
    informational_categories: list[str]
    cvr_branch_codes: dict[str, list[str]]
    web_search_terms: dict[str, str]
    web_search_monthly_budget: int
    web_search_max_calls_per_run: int
    cvr_cache_max_age_days: int
    boligsiden: BoligsidenSettings

    @property
    def all_categories(self) -> list[str]:
        return [*self.hard_filter_categories, *self.informational_categories]


def load_settings(path: Path | str = DEFAULT_CONFIG_PATH) -> Settings:
    raw = yaml.safe_load(Path(path).read_text())
    boligsiden_raw = raw["boligsiden"]
    return Settings(
        water_km=float(raw["water_km"]),
        hangout_km=float(raw["hangout_km"]),
        grocery_km=float(raw["grocery_km"]),
        min_lake_area_ha=float(raw["min_lake_area_ha"]),
        lake_strict=bool(raw["lake_strict"]),
        amenity_search_radius_km=float(raw["amenity_search_radius_km"]),
        postal_ranges=[tuple(r) for r in raw["postal_ranges"]],
        fritidsbolig_address_type=raw.get("fritidsbolig_address_type"),
        hard_filter_categories=list(raw["hard_filter_categories"]),
        informational_categories=list(raw["informational_categories"]),
        cvr_branch_codes={k: list(v) for k, v in raw.get("cvr_branch_codes", {}).items()},
        web_search_terms={k: str(v) for k, v in raw.get("web_search_terms", {}).items()},
        web_search_monthly_budget=int(raw["web_search_monthly_budget"]),
        web_search_max_calls_per_run=int(raw["web_search_max_calls_per_run"]),
        cvr_cache_max_age_days=int(raw["cvr_cache_max_age_days"]),
        boligsiden=BoligsidenSettings(
            base_url=boligsiden_raw["base_url"],
            requests_per_second=float(boligsiden_raw["requests_per_second"]),
            per_page=int(boligsiden_raw["per_page"]),
            user_agent=boligsiden_raw["user_agent"],
        ),
    )
