# Handoff — Summer House Screener

Read this first if you're picking this project back up. It's written for a fresh Claude
session with no memory of the conversation that built this — probably you, on desktop, with
real internet access. That last part matters: **the session that wrote this code had almost
no internet egress** (only PyPI/npm/GitHub/the Anthropic API were reachable — not boliga.dk,
not download.geofabrik.de, not cvrapi.dk, not even example.com). Everything below that says
"unverified" means exactly that: written carefully against documented/plausible shapes and
isolated behind one clean seam, but never actually hit a real server. Your first and highest-
value job is closing those gaps now that you can actually reach the internet.

## tl;dr

- Branch: `claude/summer-house-screener-plan-xpq7u6` on `KasperGroesLudvigsen/workshop`.
- M0–M5 done. 49 tests pass. A real, working demo site exists (fixture data, not live data).
- M6–M10 not started.
- Full milestone detail: `docs/PLAN.md`. This file is the "what's actually true right now"
  status report and the checklist for what to do next.
- Nothing here is a design problem — it's a "go verify this against the real internet"
  problem. Read the "Do these first" section below before writing new code.

## How to get running

```bash
cd /path/to/workshop
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
PYTHONPATH=src python3 -m pytest tests/ -q          # should show 49 passed
PYTHONPATH=src python3 jobs/demo_m3.py              # writes data/site/index.html
```

Open `data/site/index.html` in a real browser (not headless) — with real internet, Leaflet
and OSM tiles will actually load, unlike in the build sandbox where the map degraded
gracefully to "table only". That's expected; it's not a bug to fix.

## Do these first — verification gaps, in priority order

These block real (non-fixture) data from flowing through the pipeline. Each one is isolated
behind a small, named seam specifically so fixing it doesn't ripple elsewhere.

### 1. Boliga API parameters (`src/screener/fetch/boliga.py`)

- `_PARAM_NAMES` (line ~40) holds the query parameter names (`zipcodeFrom`, `zipcodeTo`,
  `page`, `pageSize`, `sort`, `propertyType`) and `_extract_total_count`/`_extract_listings`
  hold the candidate response-key names (`meta.totalCount`, `results`, etc). These are
  secondhand (public write-ups about this undocumented API), not verified.
- `config/thresholds.yaml`'s `boliga_property_type_fritidsbolig` is `null`.
- **Action**: open boliga.dk, filter to Fritidshus, watch DevTools → Network → Fetch/XHR,
  and confirm/correct both. Faster alternative that needs no DevTools session at all: call
  `discover_property_types()` in that same file against an unfiltered search for a mixed
  postal code — it reads the type code straight off Boliga's own response labels. Either
  way, set the config value once confirmed, and fix `_PARAM_NAMES`/the key-candidate tuples
  if reality differs.
- Also sanity-check `normalize_listing()`'s field-name candidates (`_first_present` calls)
  against a real response.

### 2. Real OSM extract (`src/screener/fetch/osm_extract.py`)

- Never run against real data — only against `tests/fixtures/sample.osm.xml`, a hand-built
  fixture with real Sjælland coordinates but fake features.
- **Action**: `python -m screener.fetch.osm_extract --download --pbf data/denmark-latest.osm.pbf --out data/osm_store.pkl`
  (or call `download_geofabrik_extract` / `build_geometry_store` directly). Sanity-check the
  resulting `GeometryStore` layer sizes look plausible (thousands of lakes, not zero; a
  connected coastline, not fragments only in one corner) before trusting it.
- Known simplification already flagged in the code: only simple closed *ways* become lake
  polygons; multipolygon *relations* (a minority of large/complex lakes) are skipped with a
  logged count. Check that count after a real run — if it's large, revisit with
  `osmium.area.MultipolygonManager`.

### 3. Bathing water dataset (`src/screener/fetch/bathing_water.py`)

- `BADEVAND_SOURCE_URL` and `_COLUMNS` (the expected CSV column names) are placeholders —
  genuinely guessed at a plausible shape, not found via a real download.
- **Action**: find Miljøstyrelsen's or the EEA's actual current distribution for Danish
  bathing water designations (badevand.dk, or the EEA's WISE bathing water dataset), update
  the URL/column mapping, and re-run `load_badevand_sites` against it. `load_badevand_sites`
  already raises loudly (`KeyError`) if columns don't match — that's intentional, don't
  soften it, just fix the mapping.

### 4. Address validation — DAWA's replacement (`src/screener/resolve/address_regex.py`)

- **This is the one genuinely blocking item for M5 to do anything for real.** DAWA (Denmark's
  address API) shut down 2026-07-01. The brief names "Adressevælger" as a possible
  replacement but the build session's knowledge cutoff (Jan 2026) predates the shutdown, and
  it had no internet to check what actually replaced it.
- `AddressValidator` is a `Protocol` (see the class right above `NotConfiguredValidator`);
  the default implementation raises `NotImplementedError` rather than silently accepting
  anything. **Action**: find the real replacement service, write a class implementing
  `.validate(candidate) -> ValidatedAddress | None` against it, and pass instances of it
  wherever `resolve.pipeline.resolve_business_address(..., validator=...)` is called. Nothing
  else in `resolve/` needs to change — this is a one-class swap.

### 5. cvrapi.dk field names (`src/screener/fetch/cvr.py`)

- `_parse_company()`'s field-name guesses (`vat`/`cvr`, `names`/`binames`/`secondaryname` for
  trading names, `zipcode`, `enddate`) are from public documentation/community usage of
  cvrapi.dk, not a live call.
- **Action**: make one real `search_by_name` call, compare the actual JSON shape, fix field
  names if they differ. Also add a real contact identifier to `DEFAULT_USER_AGENT` — it's
  currently a placeholder string and cvrapi.dk asks callers to identify themselves.
- **Worth remembering**: cvrapi.dk is a lookup API (name/vat → one record), not a bulk
  enumeration API. It can validate a candidate hangout/grocery name into an address; it
  cannot answer "list every hangout in postal code 4200". That enumeration still needs OSM
  POI extraction and/or M7's web-search gap-fill to produce candidate names first.

## Decisions already made (don't re-litigate without new information)

- **Stack**: Python, shapely 2.0 (STRtree, no separate `rtree` package needed) + pyproj +
  DuckDB + pyosmium + curl_cffi + Jinja2.
- **CVR access**: cvrapi.dk (free, no registration) over the official Datafordeleren API
  (needs a service agreement) — a deliberate low-setup-cost choice, with the
  lookup-vs-enumeration trade-off noted above.
- **Notifications**: ntfy (topic name, no account) over SMTP.
- **Lake eligibility default**: strict (`lake_strict: true` in config), with both strict and
  loose always computed so the UI toggle needs no re-score.
- **Min lake area**: 5 ha, configurable.
- **Distance function**: straight-line haversine now (`geo/distance.py`), deliberately
  isolated behind `get_distance_fn()` so a drive-time backend can replace it later.
- **Build order**: thin vertical slice (M0-M3) before layering in CVR/bathing-water/etc —
  this succeeded; M3's demo site was real and interactive before any business data existed.

## What's not started at all

- **M6 (findsmiley)**: `fetch/findsmiley.py` doesn't exist yet. Should fetch a business's
  Fødevarestyrelsen inspection report (found via the findsmiley link `resolve/jsonld.py`'s
  `find_findsmiley_link` already extracts during JSON-LD parsing) and attach inspection date
  + address cross-check to resolved businesses.
- **M7 (web-search gap-fill)**: `fetch/web_search.py` doesn't exist. Needs a **Brave Search
  API key** (not yet obtained — get one before starting this). Search per town, not per
  listing; feed discovered names through the *existing* `resolve/pipeline.py` unchanged —
  never skip the address-register gate just because a name came from search.
- **M8 (nightly orchestration)**: `jobs/nightly.py` doesn't exist. Should tie together fetch
  (M1) → score (M3-M7) → diff against yesterday (use `db.py`'s `save_scored_listings` and
  `passing_listing_ids`, already implemented) → notify via ntfy on new passes.
- **M9 (site polish), remainder**: the template (`site/templates/index.html.j2`) already has
  live typed-input filtering for price/rooms/m²/lot/build-year/the three thresholds/lake-
  strict, all client-side with no page reload — verified working via headless browser render.
  Still missing: per-category **minimum-count** filter inputs ("at least 3 hangouts within
  threshold"), and table columns for every field per the brief ("all columns visible") —
  address, energy class, days on market, and the four CVR categories that exist in the data
  schema but aren't columns yet (fish_shop, wine_shop, ice_cream, butcher) are currently
  absent from the table. Also: everything needs re-testing against real data once items 1-5
  above are fixed — all current verification used fixtures.
- **M10 (deployment)**: nothing written. Needs systemd `.service`/`.timer` units for both
  jobs and a Hetzner runbook (CX22 sizing note, required env vars, one-time manual steps).
  Actual server provisioning is the user's action, not something to automate.

## Credentials / access needed from the user (not solvable by writing more code)

- **Brave Search API key** — required before M7 can do anything for real.
- **Hetzner account + server** — required before M10's deployment steps can be executed
  (the runbook can be written without it, but not run).
- **ntfy topic name** — trivial, just needs the user (or you) to pick a string.
- A real contact email/identifier for the Boliga and cvrapi.dk User-Agent strings — both are
  currently placeholder text in `fetch/boliga.py` (`_DEFAULT_...`) and `fetch/cvr.py`
  (`DEFAULT_USER_AGENT`).
- **`ANTHROPIC_API_KEY`** env var if you want `resolve/llm_extract.py`'s last-resort
  extraction step to actually run (install the optional `llm` extra: `pip install -e ".[llm]"`).
  This is the least urgent gap — steps 1-3 of the resolution pipeline should resolve most
  businesses before this ever triggers.

## Test suite map (49 tests, all passing on fixtures)

| File | Covers |
|---|---|
| `test_boliga.py` | Sharding, 300-cap bisection, truncation assertion, 403 handling, `discover_property_types` |
| `test_osm_extract.py` | pyosmium extraction, area computation, spatial queries against the synthetic fixture |
| `test_water.py` | Lake area filtering, eligibility signals (badevand/swimming_area/beach), strict vs. loose divergence |
| `test_bathing_water.py` | CSV schema validation, spatial site-to-lake matching, coastal sites excluded |
| `test_cvr.py`, `test_cvr_match.py` | CVR client parsing, fuzzy name matching, closed-business rejection |
| `test_jsonld.py` | LocalBusiness/PostalAddress extraction, `@graph`-wrapped JSON-LD, findsmiley link extraction |
| `test_address_regex.py` | Danish address regex, the `NotConfiguredValidator` raising rather than accepting |
| `test_resolve_pipeline.py` | All 4 resolution steps in order, and — the important one — a hallucinated LLM address being offered to the validator and dropped |
| `test_score_pipeline.py` | End-to-end scoring: water hard filter, OSM categories, business-directory wiring, sort order |

`tests/fixtures/sample.osm.xml` and `tests/fixtures/badevand_sample.csv` are hand-built,
real-Sjælland-coordinates synthetic data (near Sorø) — useful reference for what a realistic
lake/coastline/marina layout looks like if you need more fixture data later.

## Suggested order for the next session

1. Fix items 1–5 above, in that order (Boliga params fastest/highest-value; DAWA replacement
   is the one that actually blocks M5 from doing anything real).
2. Re-run the full test suite — it shouldn't need changes, since it's fixture-based, but
   confirm nothing broke.
3. Run `jobs/demo_m3.py`-equivalent against one real postal code shard end to end; eyeball
   the result against a real map before trusting it further.
4. M6 → M7 (get the Brave key first) → M8 → M9 remainder → M10.
