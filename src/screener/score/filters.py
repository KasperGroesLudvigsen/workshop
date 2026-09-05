"""Hard-filter predicates.

A listing is excluded if it fails water, hangout, or grocery. Each predicate
returns a plain bool so the pipeline can log *why* a listing failed, and the
site can ship both the strict and loose water verdicts so the lake-strict
UI toggle never needs a re-score.

M3 status: hangout and grocery aren't wired up yet (they need CVR business
resolution, landing in M5) — ``passes_hangout``/``passes_grocery`` are
``None`` ("not yet evaluated") rather than ``True``/``False``, and are
excluded from ``overall_passed`` until real data exists. Water is fully
enforced now.
"""
from __future__ import annotations

from screener.geo.water import WaterResult


def passes_water_strict(water: WaterResult, water_km: float) -> bool:
    return water.open_water_km_strict <= water_km


def passes_water_loose(water: WaterResult, water_km: float) -> bool:
    return water.open_water_km_loose <= water_km


def passes_category(nearest_km: float | None, threshold_km: float) -> bool | None:
    if nearest_km is None:
        return None
    return nearest_km <= threshold_km


def overall_passed(*, water_strict: bool, water_loose: bool, hangout: bool | None, grocery: bool | None) -> bool:
    """A listing stays in the shipped data set if it could pass under
    either lake-strictness setting, and passes every hard filter that has
    real data behind it yet. Once hangout/grocery land (M5) this starts
    enforcing them for real; until then a None is not treated as a fail."""
    water_ok = water_strict or water_loose
    hangout_ok = hangout is not False
    grocery_ok = grocery is not False
    return water_ok and hangout_ok and grocery_ok
