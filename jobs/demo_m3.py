"""M3 checkpoint demo: runs the full fetch(fixture)->score->site pipeline
end to end and writes a real, openable static site.

This sandbox has no outbound internet access to boliga.dk or Geofabrik (see
fetch/boliga.py and fetch/osm_extract.py docstrings), so real Boliga
listings and a real Denmark OSM extract aren't available here. This script
proves the pipeline itself is correct and wired together end to end using:

  - tests/fixtures/sample.osm.xml: a small synthetic OSM extract (real
    Sjaelland coordinates near Soroe) with a coastline, an eligible lake
    (swimming_area + beach signals), an ineligible lake, a too-small pond,
    a marina, a playground, and a pool.
  - a handful of synthetic listings placed relative to that fixture, some
    passing the water filter and some failing it.

Once a real OSM extract and real Boliga listings are available (M1/M2 run
for real on a networked machine), swap the two inputs below for
`build_geometry_store(real_pbf_path)` and normalized real Boliga listings —
nothing else in score/pipeline.py or site/build.py changes.
"""
from __future__ import annotations

from pathlib import Path

from screener.config import REPO_ROOT, load_settings
from screener.fetch.osm_extract import build_geometry_store
from screener.score.pipeline import score_listings, sort_scored_listings
from screener.site.build import build_site

FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "sample.osm.xml"

# Synthetic listings: (id, lat, lon, price, size_m2, lot_size_m2, rooms, build_year, address)
DEMO_LISTINGS = [
    ("demo-1", 55.435188, 11.559821, 2_195_000, 68, 850, 3, 1974, "Sostien 4, 4243 Rude"),
    ("demo-2", 55.429518, 11.551550, 1_850_000, 54, 620, 2, 1968, "Havnevej 12, 4243 Rude"),
    ("demo-3", 55.426166, 11.518141, 1_650_000, 72, 900, 4, 1981, "Skovbrynet 7, 4243 Rude"),
    ("demo-4", 55.774067, 12.210372, 995_000, 45, 500, 2, 1960, "Langt Vaek 1, 4990 Sakskobing"),
]


def build_demo_listings() -> list[dict]:
    return [
        {
            "id": lid,
            "lat": lat,
            "lon": lon,
            "price": price,
            "size_m2": size_m2,
            "lot_size_m2": lot_m2,
            "rooms": rooms,
            "build_year": year,
            "address": address,
            "url": f"https://www.boliga.dk/bolig/{lid}",
        }
        for lid, lat, lon, price, size_m2, lot_m2, rooms, year, address in DEMO_LISTINGS
    ]


def main() -> None:
    settings = load_settings()
    store = build_geometry_store(FIXTURE)
    listings = build_demo_listings()
    scored = score_listings(listings, store, settings)
    scored = sort_scored_listings(scored)

    out_path = REPO_ROOT / "data" / "site" / "index.html"
    build_site(scored, settings, out_path)

    passed = [r for r in scored if r["passed"]]
    print(f"scored {len(scored)} demo listings, {len(passed)} pass the water hard filter")
    print(f"site written to {out_path}")


if __name__ == "__main__":
    main()
