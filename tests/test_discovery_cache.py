from __future__ import annotations

import os
import time
from datetime import timedelta

from screener.fetch.discovery_cache import is_stale, load_cache, load_or_build, save_cache
from screener.resolve.pipeline import ResolvedBusiness

_SAMPLE = {
    "hangout": [ResolvedBusiness(name="Test Kro", street="Havnevej 1", postal_code="4243", town="Rude", lat=55.2, lon=11.5, source_step="cvr_discovery")],
}


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "cache.pkl"
    save_cache(_SAMPLE, path)
    loaded = load_cache(path)
    assert loaded == _SAMPLE


def test_is_stale_when_cache_missing(tmp_path):
    assert is_stale(tmp_path / "does-not-exist.pkl") is True


def test_is_stale_when_source_is_newer(tmp_path):
    cache_path = tmp_path / "cache.pkl"
    source_path = tmp_path / "source.pbf"
    save_cache(_SAMPLE, cache_path)
    time.sleep(0.05)
    source_path.write_text("fresh source")
    assert is_stale(cache_path, source_path=source_path) is True


def test_not_stale_when_source_is_older(tmp_path):
    source_path = tmp_path / "source.pbf"
    source_path.write_text("old source")
    time.sleep(0.05)
    cache_path = tmp_path / "cache.pkl"
    save_cache(_SAMPLE, cache_path)
    assert is_stale(cache_path, source_path=source_path) is False


def test_stale_when_older_than_max_age(tmp_path):
    cache_path = tmp_path / "cache.pkl"
    save_cache(_SAMPLE, cache_path)
    old_time = time.time() - timedelta(days=10).total_seconds()
    os.utime(cache_path, (old_time, old_time))
    assert is_stale(cache_path, max_age=timedelta(days=7)) is True


def test_not_stale_within_max_age(tmp_path):
    cache_path = tmp_path / "cache.pkl"
    save_cache(_SAMPLE, cache_path)
    assert is_stale(cache_path, max_age=timedelta(days=7)) is False


def test_load_or_build_cache_hit_never_calls_build_fn(tmp_path):
    cache_path = tmp_path / "cache.pkl"
    save_cache(_SAMPLE, cache_path)
    calls = []

    def build_fn():
        calls.append(1)
        return {}

    result = load_or_build(cache_path, build_fn=build_fn)
    assert result == _SAMPLE
    assert calls == []


def test_load_or_build_cache_miss_calls_build_fn_and_saves(tmp_path):
    cache_path = tmp_path / "cache.pkl"
    calls = []

    def build_fn():
        calls.append(1)
        return _SAMPLE

    result = load_or_build(cache_path, build_fn=build_fn)
    assert result == _SAMPLE
    assert calls == [1]
    assert cache_path.exists()
    assert load_cache(cache_path) == _SAMPLE


def test_load_or_build_force_rebuild_ignores_fresh_cache(tmp_path):
    cache_path = tmp_path / "cache.pkl"
    save_cache(_SAMPLE, cache_path)
    calls = []

    def build_fn():
        calls.append(1)
        return {"grocery": []}

    result = load_or_build(cache_path, build_fn=build_fn, force_rebuild=True)
    assert calls == [1]
    assert result == {"grocery": []}
