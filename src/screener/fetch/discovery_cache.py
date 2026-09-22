"""Generic pickle-backed cache for expensive, listing-independent discovery
passes (OSM POI extraction, CVR bulk discovery+resolution). Both discover
businesses across the whole configured region, independent of which
listings exist in any given run -- re-running the full pass every time
`jobs/run_real.py` is invoked (a few times a week, to pick up new listings)
wastes ~20 minutes of pyosmium and a rate-limited DAR validation pass for
no reason, since neither source actually changes that often.

Mirrors the save/load pattern `geo/store.py`'s ``GeometryStore`` already
uses for the sibling OSM geometry pass -- pickle, no new serialization
approach introduced.
"""
from __future__ import annotations

import logging
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from screener.resolve.pipeline import ResolvedBusiness

logger = logging.getLogger(__name__)

BusinessesByCategory = dict[str, list[ResolvedBusiness]]


def save_cache(data: BusinessesByCategory, path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(data, f)


def load_cache(path: Path | str) -> BusinessesByCategory:
    with open(path, "rb") as f:
        return pickle.load(f)


def is_stale(
    cache_path: Path | str, *, source_path: Path | str | None = None, max_age: timedelta | None = None,
) -> bool:
    """True if ``cache_path`` doesn't exist, or ``source_path`` exists and is
    newer than it (a fresher source -- e.g. a re-downloaded OSM extract --
    is available), or ``max_age`` is set and the cache is older than that."""
    cache_path = Path(cache_path)
    if not cache_path.exists():
        return True
    cache_mtime = cache_path.stat().st_mtime
    if source_path is not None:
        source_path = Path(source_path)
        if source_path.exists() and source_path.stat().st_mtime > cache_mtime:
            return True
    if max_age is not None:
        age = datetime.now() - datetime.fromtimestamp(cache_mtime)
        if age > max_age:
            return True
    return False


def load_or_build(
    cache_path: Path | str,
    *,
    build_fn: Callable[[], BusinessesByCategory],
    source_path: Path | str | None = None,
    max_age: timedelta | None = None,
    force_rebuild: bool = False,
) -> BusinessesByCategory:
    cache_path = Path(cache_path)
    if not force_rebuild and not is_stale(cache_path, source_path=source_path, max_age=max_age):
        logger.info("discovery_cache: using cached result from %s", cache_path)
        return load_cache(cache_path)
    logger.info("discovery_cache: %s missing/stale/force-rebuilt, running the real pass", cache_path)
    result = build_fn()
    save_cache(result, cache_path)
    return result
