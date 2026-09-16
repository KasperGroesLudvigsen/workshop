# Summer House Location Screener — Build Plan

> Status as of the last update: **M0–M5 done**, plus real hangout/grocery
> business discovery (2026-09-16, via Erhvervsstyrelsen's `cvr-permanent` --
> a chunk of M6/M7's actual value without needing OSM-POI extraction or
> Brave web-search). M6–M10 remainder not started. As of 2026-09-15, all 5
> real-data verification gaps below are closed, and the listings source was
> switched from Boliga to Boligsiden after Boliga's Cloudflare protection
> proved unreliable for a real run. See `docs/HANDOFF.md` for the detailed
> status report.

## Context

The user wants a filter, not a recommender: pull fritidsbolig listings across
Sjælland/Lolland/Falster/Møn, drop everything that fails three hard geographic thresholds
(open water, hangout, grocery), and surface the survivors on a static map+table site with
live-adjustable thresholds, counts, and named establishments. The repo started empty (just
a README) — this was a from-scratch build. The original brief was unusually complete (it
already answered most architecture questions), so this plan's job was to turn it into a
concrete file layout and a buildable sequence, not to re-litigate its decisions.

Stack: **Python** (shapely/pyproj, DuckDB, requests, pyosmium). Build order: a
**thin vertical slice first** (listings fetch → OSM-only distance filter → static site), then
layer in CVR business resolution, bathing-water eligibility, findsmiley, and web-search
gap-fill. The listings source itself started as Boliga (M1) and was replaced with Boligsiden
2026-09-15 — see M1 below and `docs/HANDOFF.md`.

## Repo layout

```
/config
  thresholds.yaml          # all distances, min lake area ha, strictness default — single source of truth
/src/screener
  /fetch                   # stage 1: raw acquisition, no scoring logic
    boligsiden.py          # paginated search client + client-side postal-range filter, rate-limited
    _rate_limit.py         # shared RateLimiter (boligsiden.py + cvr.py)
    osm_extract.py         # Geofabrik download + osmium/pyosmium filtering into a local spatial store
    cvr.py                 # cvrapi.dk lookup client (name/vat search)
    cvr_discovery.py       # cvr-permanent bulk enumeration by branch code + postal code (additive to cvr.py)
    bathing_water.py       # Miljøstyrelsen/EEA badevand dataset pull + spatial lake matching
    findsmiley.py          # NOT STARTED — per-business inspection report fetch (M6)
    web_search.py          # NOT STARTED — Brave search, per-town discovery (M7)
  /resolve                 # business name -> validated address pipeline (fixed, non-agentic)
    cvr_match.py           # step 1: fuzzy name match incl. binavne/trading names
    cvr_discovery.py       # bulk counterpart to cvr_match.py: raw cvr-permanent hits -> validated businesses
    jsonld.py              # step 2: LocalBusiness/PostalAddress parse + findsmiley link-follow
    address_regex.py       # step 3: candidate extraction + address-register validation
    llm_extract.py         # step 4: last-resort structured extraction (Anthropic API)
    pipeline.py            # orchestrates 1->4, stopping at first validated hit; loud discard on total failure
  /geo
    distance.py            # distance_fn(a, b) -> km; straight-line now, swappable for drive-time later
    projection.py          # WGS84 <-> EPSG:25832 (Denmark UTM) conversion
    store.py               # Layer/GeometryStore: STRtree-backed nearest/within/intersects queries
    water.py               # sea + lake-shore distance, min-area filter, lake eligibility + reason
    amenities.py           # nearest-distance + within-radius candidate list, per category
    business_directory.py  # turns resolved CVR businesses into a queryable Layer
  /score
    filters.py             # hard-filter predicates (water/hangout/grocery)
    pipeline.py             # raw listings + indices -> scored records, recompute-safe
  /site
    build.py               # embeds scored JSON, renders index.html (Leaflet + table)
    maplinks.py            # Google/Apple map link builders
    templates/index.html.j2
  /notify                  # NOT STARTED — ntfy.py (M8)
  db.py                    # DuckDB schema + raw-response cache, scored-listings persistence
  config.py                # loads thresholds.yaml, exposes typed Settings
/jobs
  demo_m3.py               # end-to-end demo against synthetic fixtures (proves the pipeline wiring)
  run_real.py              # one-shot real run: live Boligsiden + real OSM/bathing-water -> site
  static_prep.py           # NOT STARTED — monthly/on-demand: OSM index, CVR pull+resolve, bathing-water pull (M8-ish)
  nightly.py               # NOT STARTED — fetch -> score -> diff -> notify (M8)
/deploy                    # NOT STARTED — systemd units + Hetzner runbook (M10)
/tests                     # 62 tests, all passing
```

## Key design decisions (carried from the brief, made concrete)

- **Fetch/score separation**: `fetch/*` only writes raw JSON to disk/db keyed by
  query+timestamp hash; `score/pipeline.py` reads only from persisted raw data. Changing a
  threshold in `thresholds.yaml` re-runs `score/pipeline.py` alone — never re-scrapes.
- **Config-driven thresholds**: `hangout_km`, `grocery_km`, `water_km`, `min_lake_area_ha`,
  `lake_strict` all live in `config/thresholds.yaml` and are read by both the batch scorer
  and embedded into the site's JSON so the client can recompute counts live.
- **Distance function is swappable**: `geo/distance.py` exposes one `distance_fn`
  signature; straight-line (haversine) now, so a drive-time backend can replace it later
  without touching callers.
- **Water eligibility**: `geo/water.py` computes `sea_distance_km` and lake shore distance
  as separate columns plus `open_water_km = min(...)`. Each candidate lake carries
  `eligible: bool` and `eligibility_reason: {badevand, swimming_area, beach_on_shore, none}`,
  and area is filtered by `min_lake_area_ha` before eligibility is even checked.
  `open_water_km_strict` and `open_water_km_loose` are both always computed and shipped to
  the site so the lake-strictness UI toggle is instant, never a re-score.
- **Counts/names are threshold-relative, not baked in**: for each category, the JSON embeds
  a sorted list of `(name, distance_km)` per listing within a generous outer search radius
  (`amenity_search_radius_km`), not just a count at the default threshold. The site
  recomputes count/names client-side for whatever radius is currently set.
- **Business resolution pipeline is a fixed pipeline, not a loop**: `resolve/pipeline.py`
  tries steps 1→4 in order and stops at the first address that validates against the address
  register. Anything that fails all four steps is dropped with a logged reason — never
  geocode raw text, model output included. Every candidate (CVR, JSON-LD, regex, LLM) is
  run through the *same* validator gate.
- **Boligsiden safety**: `fetch/boligsiden.py` enforces a considerate rate limit, treats
  HTTP 403/429 as a hard failure (raises, never degrades to empty results), and asserts the
  number of *unique* cases collected against the response's declared `totalHits` — a
  duplicate `caseID` across pages (confirmed to happen live, at national scale, as the
  underlying sorted dataset shifts mid-crawl) is logged and skipped, not treated as
  truncation. No postal-range sharding needed: Boligsiden has no small per-query cap the way
  Boliga did, so the client just pages through the whole country and filters by zip code
  client-side.

## Build sequence (vertical slices)

**M0 — Scaffolding.** ✅ Done. `pyproject.toml`, `config/thresholds.yaml`, `db.py` (DuckDB
raw-response cache + scored-listings table), `config.py` (typed settings loader).

**M1 — Listings fetch.** ✅ Done. Originally built against Boliga (params confirmed live
2026-09-15), then **replaced with Boligsiden same day** after Boliga's Cloudflare protection
proved unreliable for a real run — see `docs/HANDOFF.md`. `fetch/boligsiden.py` pages through
the whole country per `addressType` (no sharding needed, unlike Boliga) and filters to the
configured postal ranges client-side; loud truncation/duplicate handling, rate limiting.
`fritidsbolig_address_type: "holiday house"` and the field mapping are confirmed against the
live API.

**M2 — OSM static prep.** ✅ Done, **run against the real Geofabrik extract 2026-09-15**.
pyosmium extraction into a projected (EPSG:25832), STRtree-indexed `GeometryStore` covering
coastline/beach/lake/reservoir/marina/playground/pool/swimming_area. Real Denmark extract
yields 29697 lakes, 2230 coastline segments, plausible counts across the board — see
`docs/HANDOFF.md`. Also still covered by the synthetic fixture
(`tests/fixtures/sample.osm.xml`) for unit tests.

**M3 — First runnable slice.** ✅ Done, and now also verified against real data. Water
scoring + OSM-backed amenity categories wired through `score/pipeline.py` into a Leaflet+table
static site. `jobs/demo_m3.py` (synthetic fixtures) verified via headless-browser render:
table population, live sort, live threshold filtering, strict/loose lake toggle.
`jobs/run_real.py` (real Boligsiden + real OSM/bathing-water data) verified 2026-09-15: 2,428
real listings across the full configured region, opened in a real browser — pins cluster
along the coast exactly where fritidshuse actually are.

**M4 — Lake eligibility.** ✅ Done, **badevand dataset confirmed live 2026-09-15, and the
original URL/schema were wrong, not just unverified**. `fetch/bathing_water.py` now pulls
Danmarks Miljøportal's public WFS (`puls:Badevand`), not the old `badevand.dk` guess, and
spatially matches designated bathing-water sites to lake polygons (not by name — names
differ between OSM and official sources). OSM `swimming_area` and beach-on-shore detection
now run against the real M2 extract.

**M5 — CVR + business resolution.** ✅ Done, **cvrapi.dk field names confirmed live
2026-09-15** (and a real quota-vs-not-found bug fixed — see `docs/HANDOFF.md`). `cvrapi.dk`
itself still can't do bulk discovery (it's a lookup API, not an enumeration API) — but
**2026-09-16, `fetch/cvr_discovery.py` + `resolve/cvr_discovery.py` added real bulk discovery
via Erhvervsstyrelsen's `cvr-permanent` system-til-system access** (branch code + postal code
enumeration), wired into `jobs/run_real.py`. Hangout/grocery are now real hard filters end to
end for CVR-registered businesses — OSM POIs / M7's web-search gap-fill are only still needed
for businesses *not* registered in CVR at all. `resolve/pipeline.py` implements all 4 steps
with the address-register validation gate; `AddressValidator`'s real implementation,
`DatafordelerAddressValidator`, is confirmed live against DAR's GraphQL v3 endpoint (see
`docs/HANDOFF.md`) — reused unchanged by both the name-lookup and bulk-discovery paths, now
rate-limited (5 req/sec default) since bulk discovery gives it far higher call volume than
before. `score/pipeline.py` enforces hangout/grocery as real hard filters once a business
directory is supplied — verified live for one postal code (see `docs/HANDOFF.md`); a
full-region run with discovery wired in hasn't been attempted yet.

**M6 — findsmiley.** ⬜ Not started. Attach inspection date + address cross-check as a
freshness/secondary signal on food businesses already resolved in M5.

**M7 — Web-search gap-fill.** ⬜ Not started. Needs a Brave Search API key. Search per town
for hangouts/groceries CVR/OSM missed; feed discovered names through the same M5 resolution
pipeline (never skip the address-register gate for search-discovered names).

**M8 — Nightly orchestration.** ⬜ Not started. `jobs/nightly.py` = fetch → score → diff
against yesterday's scored table (`db.py` already has `passing_listing_ids`/
`save_scored_listings` for this) → notify via ntfy on new passing listings.

**M9 — Site polish.** 🟡 Partially done. The template already has typed inputs for price
range, min rooms, min m², min lot size, min build year, plus the three threshold distances
and the lake-strict toggle, all live-filtering. **Still missing**: per-category minimum-count
filter inputs ("at least 3 hangouts within threshold"), table columns for every field
(address, energy class, days on market, and the four not-yet-populated CVR categories —
fish_shop/wine_shop/ice_cream/butcher — aren't shown yet), and testing against real data
once M1/M2 are pointed at live sources.

**M10 — Deployment artifacts.** ⬜ Not started. systemd `.service`/`.timer` units for both
jobs, a Hetzner runbook (CX22 sizing, env vars needed, one-time manual steps). Actual server
provisioning happens on the user's side.

## Explicitly flagged gaps

See `docs/HANDOFF.md` for the full, current list with exact file/line pointers.

## Verification

- M1: run fetch for the full configured region, confirm raw JSON persisted, confirm 403/429
  raises loudly, confirm the unique-count-vs-`totalHits` assertion fires on a truncated
  fixture. **Done against fakes and against the live API (2026-09-15) — 2,428 real listings
  fetched, field mapping confirmed correct.**
- M3: run the full slice on real data for the configured region, open the generated
  `index.html` locally, confirm pins + table match a handful of listings checked by hand
  against a map. **Done, 2026-09-15 — see M3 above.**
- M4: unit-test `eligibility_reason` assignment against constructed fixtures for each of the
  three signals plus the "none" case. **Done.**
- M5: unit tests for `resolve/pipeline.py` covering each of the 4 steps succeeding/failing in
  order, and the "never geocode raw LLM output" gate. **Done.**
- M8: run `jobs/nightly.py` twice back-to-back with no new listings and confirm no
  notification fires; then inject a synthetic new-passing listing and confirm it does.
- M9: manually exercise the site — change the hangout radius input and confirm both the
  count column and the hard-filter result set update without a page reload. **Done for the
  thresholds that exist; redo once per-category count filters are added.**
