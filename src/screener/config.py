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
class BoligaSettings:
    base_url: str
    requests_per_second: float
    max_results_per_query: int
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
    boliga_property_type_fritidsbolig: int | None
    hard_filter_categories: list[str]
    informational_categories: list[str]
    boliga: BoligaSettings

    @property
    def all_categories(self) -> list[str]:
        return [*self.hard_filter_categories, *self.informational_categories]


def load_settings(path: Path | str = DEFAULT_CONFIG_PATH) -> Settings:
    raw = yaml.safe_load(Path(path).read_text())
    boliga_raw = raw["boliga"]
    return Settings(
        water_km=float(raw["water_km"]),
        hangout_km=float(raw["hangout_km"]),
        grocery_km=float(raw["grocery_km"]),
        min_lake_area_ha=float(raw["min_lake_area_ha"]),
        lake_strict=bool(raw["lake_strict"]),
        amenity_search_radius_km=float(raw["amenity_search_radius_km"]),
        postal_ranges=[tuple(r) for r in raw["postal_ranges"]],
        boliga_property_type_fritidsbolig=raw.get("boliga_property_type_fritidsbolig"),
        hard_filter_categories=list(raw["hard_filter_categories"]),
        informational_categories=list(raw["informational_categories"]),
        boliga=BoligaSettings(
            base_url=boliga_raw["base_url"],
            requests_per_second=float(boliga_raw["requests_per_second"]),
            max_results_per_query=int(boliga_raw["max_results_per_query"]),
            user_agent=boliga_raw["user_agent"],
        ),
    )
