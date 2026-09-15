"""Shared politeness throttle for fetch clients (Boligsiden, cvrapi.dk).

Not a hard API-enforced limit in either case -- a considerate default so a
re-run during development (or a real nightly job) never hammers a free,
unauthenticated third-party API just because it happens to tolerate it.
"""
from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, requests_per_second: float):
        self._min_interval = 1.0 / requests_per_second
        self._last_call: float | None = None

    def wait(self) -> None:
        if self._last_call is not None:
            remaining = self._min_interval - (time.monotonic() - self._last_call)
            if remaining > 0:
                time.sleep(remaining)
        self._last_call = time.monotonic()
