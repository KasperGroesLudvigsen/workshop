"""DuckDB-backed persistence.

Two concerns live here, kept deliberately separate:

- **Raw response cache** (`raw_responses`): every fetch stage (Boliga, CVR,
  bathing water, ...) writes its untouched response body here, keyed by a
  hash of (source, url, params). The scorer reads only from this table -
  changing a threshold in config/thresholds.yaml re-runs scoring from disk,
  never re-fetches. Re-running a fetch with identical params is a cache hit
  and skipped, so re-running `jobs/nightly.py` during development doesn't
  hammer Boliga.
- **Scored listings** (`scored_listings`): one JSON blob per listing per run
  date, so the nightly job can diff today's passing listings against
  yesterday's without re-scoring.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import duckdb

from screener.config import REPO_ROOT

DEFAULT_DB_PATH = REPO_ROOT / "data" / "screener.duckdb"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_responses (
    cache_key    VARCHAR PRIMARY KEY,
    source       VARCHAR NOT NULL,
    url          VARCHAR NOT NULL,
    params       VARCHAR NOT NULL,
    status_code  INTEGER NOT NULL,
    fetched_at   TIMESTAMP NOT NULL,
    body         VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS scored_listings (
    listing_id   VARCHAR NOT NULL,
    run_date     DATE NOT NULL,
    passed       BOOLEAN NOT NULL,
    data         VARCHAR NOT NULL,
    PRIMARY KEY (listing_id, run_date)
);
"""


def cache_key(source: str, url: str, params: dict[str, Any] | None) -> str:
    """Stable hash for a fetch call, used as the raw_responses primary key."""
    payload = json.dumps({"source": source, "url": url, "params": params or {}}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self.path))
        self._con.execute(_SCHEMA)

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- raw response cache -------------------------------------------------

    def get_cached_response(self, key: str) -> str | None:
        row = self._con.execute(
            "SELECT body FROM raw_responses WHERE cache_key = ?", [key]
        ).fetchone()
        return row[0] if row else None

    def save_raw_response(
        self,
        *,
        source: str,
        url: str,
        params: dict[str, Any] | None,
        status_code: int,
        body: str,
        key: str | None = None,
    ) -> str:
        key = key or cache_key(source, url, params)
        self._con.execute(
            """
            INSERT INTO raw_responses (cache_key, source, url, params, status_code, fetched_at, body)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (cache_key) DO UPDATE SET
                status_code = excluded.status_code,
                fetched_at = excluded.fetched_at,
                body = excluded.body
            """,
            [key, source, url, json.dumps(params or {}, sort_keys=True), status_code,
             datetime.now(timezone.utc), body],
        )
        return key

    def iter_raw_responses(self, source: str) -> Iterator[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT cache_key, url, params, status_code, fetched_at, body "
            "FROM raw_responses WHERE source = ? ORDER BY fetched_at",
            [source],
        ).fetchall()
        for cache_key_, url, params, status_code, fetched_at, body in rows:
            yield {
                "cache_key": cache_key_,
                "url": url,
                "params": json.loads(params),
                "status_code": status_code,
                "fetched_at": fetched_at,
                "body": body,
            }

    # -- scored listings ------------------------------------------------------

    def save_scored_listings(self, run_date: date, records: Iterable[dict[str, Any]]) -> None:
        rows = [
            (r["listing_id"], run_date, bool(r["passed"]), json.dumps(r, default=str))
            for r in records
        ]
        self._con.executemany(
            """
            INSERT INTO scored_listings (listing_id, run_date, passed, data)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (listing_id, run_date) DO UPDATE SET
                passed = excluded.passed,
                data = excluded.data
            """,
            rows,
        )

    def passing_listing_ids(self, run_date: date) -> set[str]:
        rows = self._con.execute(
            "SELECT listing_id FROM scored_listings WHERE run_date = ? AND passed",
            [run_date],
        ).fetchall()
        return {r[0] for r in rows}

    def load_scored_listings(self, run_date: date, passed_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT data FROM scored_listings WHERE run_date = ?"
        params: list[Any] = [run_date]
        if passed_only:
            query += " AND passed"
        rows = self._con.execute(query, params).fetchall()
        return [json.loads(r[0]) for r in rows]
