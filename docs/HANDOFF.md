# Handoff — Summer House Screener

Read this first if you're picking this project back up.

## tl;dr

- Branch: `claude/summer-house-screener-plan-xpq7u6` on `KasperGroesLudvigsen/workshop`.
- M0–M5 done. 56 tests pass.
- **2026-09-15 session**: closed all 5 verification gaps from the previous handoff using real
  internet access (Boliga, cvrapi.dk, the real OSM extract, bathing water, and address
  validation via a real Datafordeler DAR account). Details below.
- M6–M10 not started.
- Full milestone detail: `docs/PLAN.md`.

## What got verified/fixed 2026-09-15

### 1. Boliga (`src/screener/fetch/boliga.py`, `config/thresholds.yaml`) — done

Confirmed live via the project's own `curl_cffi` transport (plain HTTP gets a Cloudflare
challenge page, browser impersonation is required, not optional):

- `boliga_property_type_fritidsbolig: 4` — confirmed by filtering a known holiday-cottage
  postal range (4500-4581, Sjaellands Odde) by `propertyType=4`: 376/673 results, all small
  (38-84 m²) cottages in known sommerhus villages (Nyrup, Ebbeloekke, Lumsaas).
- `_PARAM_NAMES` and `meta.totalCount`/`results` as the total-count/listings-array keys —
  all confirmed correct as originally guessed, no change needed.
- **Bug fixed**: `normalize_listing`'s `url` field was always `None` — real responses carry
  no `url`/`guid` field at all. Now builds `https://www.boliga.dk/bolig/{id}`, confirmed live
  to 200 and redirect to the canonical listing page.
- Note: the per-listing `propertyType` field in the response doesn't reliably echo back the
  filter value used, so `discover_property_types()` can't read off a label from real data
  (labels are absent from the payload). Codes were confirmed by comparing per-code result
  totals for a known area instead — see the module docstring.

### 2. cvrapi.dk (`src/screener/fetch/cvr.py`) — done

Confirmed live: `https://cvrapi.dk/api?search=...&country=dk` returns real company records
matching `_parse_company`'s existing field guesses exactly (flat `address` string, `zipcode`,
`city`, `enddate`).

- `DEFAULT_USER_AGENT` now has a real contact email.
- **Bug fixed**: cvrapi.dk signals quota-exceeded/banned as **HTTP 200 with an `error` key**,
  not a 4xx — the old code treated any `error` key the same as a 404 ("no such company"),
  silently mis-attributing "we got throttled" as "no match found". Now
  `CvrBlockedOrRateLimitedError` is raised for `QUOTA_EXCEEDED`/`BANNED`, separate from
  `CvrNotFoundError`. Regression test added (`test_quota_exceeded_is_blocked_not_not_found`).
- Note: cvrapi.dk's documented quota is 50 lookups/day/IP — low. None of the live test
  companies had a `names`/`binames`/`secondaryname` value, so that field name is still
  unconfirmed (safe no-op today, just reduces trading-name recall in `resolve/cvr_match.py`).

### 3. Real OSM extract (`src/screener/fetch/osm_extract.py`) — done

Downloaded the real 494MB Geofabrik Denmark extract and ran `build_geometry_store` for real.
Layer sizes (plausible, not fixture data): coastline 2230, beach 1460, **lake 29697**, marina
549, playground 9675, pool 1254, swimming_area 26. `data/denmark-latest.osm.pbf` and
`data/osm_store.pkl` are gitignored — re-download/rebuild locally, don't expect them in git.

### 4. Bathing water (`src/screener/fetch/bathing_water.py`) — done, and the old approach was wrong, not just unverified

`badevand.dk` (the old `BADEVAND_SOURCE_URL`) is a consumer site with no confirmed download
API. The real authoritative distribution is Danmarks Miljøportal's public GeoServer WFS layer
`puls:Badevand` at `https://pulsgeo.miljoeportal.dk/geoserver/wfs` — **no auth needed**,
license CC0 1.0 (found via `arealdata.miljoeportal.dk`'s catalog, dataset
`Badevand: Stamdata`). Rewrote the fetcher to pull WFS GeoJSON instead of a CSV. Confirmed
live: 1488 total stations, 1039 still active (449 `Closed`, now correctly dropped), 123
inland (`WaterType: "Ferskvand"`) — recognizable real lakes (Silkeborg lake district:
Ans Søbred, Bryrup Søbad, etc). Geometry comes back already in EPSG:25832 (this project's own
working CRS), so `BathingWaterSite` now stores projected `x`/`y` directly — no WGS84 round
trip. Test fixture switched from CSV to GeoJSON (`tests/fixtures/badevand_sample.geojson`).

### 5. Address validation (`src/screener/resolve/address_regex.py`) — done

DAWA (the old address API) shuts down 2026-10-01; its replacement, Datafordeleren, needed a
registered account (the user's action, now done). `DatafordelerAddressValidator` is
confirmed live against DAR's (Danmarks Adresseregister) GraphQL v3 endpoint
(`https://graphql.datafordeler.dk/DAR/v3`), reading `DATAFORDELER_DAR_API_KEY` from `.env`
(loaded automatically via `dotenv` in `screener/__init__.py` — a real shell/deploy env var
always wins over `.env`). Two round trips per candidate: `DAR_Husnummer` filtered by
`adgangsadressebetegnelse.startsWith` (DAR's only free-text filter) to find a "Gaeldende"
(status 3, i.e. current/valid — 1=Intern forberedelse, 2=Foreloebig, 4=Nedlagt) match whose
own recorded postal code (parsed from the betegnelse string — DAR's `postnummer` field is an
opaque reference id, not the 4-digit code, so it can't be filtered server-side) matches the
candidate's; then `DAR_Adressepunkt` filtered by that node's `adgangspunkt` reference id to
get its coordinate (WKT point in EPSG:25832, reprojected to WGS84). Every list query on this
schema requires either an id/rowId filter or a `virkningstid`/`registreringstid` argument —
confirmed live via a 400 without one. Verified against a real address (Refshalevej 213A,
1432 København K → resolves correctly) and two rejection cases (nonexistent address; real
street with a wrong postal code). Nothing else in `resolve/` changed — pass an instance
wherever `resolve.pipeline.resolve_business_address(..., validator=...)` is called.

## Decisions already made (don't re-litigate without new information)

- **Stack**: Python, shapely 2.0 (STRtree, no separate `rtree` package needed) + pyproj +
  DuckDB + pyosmium + curl_cffi + Jinja2.
- **CVR access**: cvrapi.dk (free, no registration) over the official Datafordeleren API
  (needs a service agreement) — a deliberate low-setup-cost choice, with the
  lookup-vs-enumeration trade-off noted below.
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
  absent from the table.
- **M10 (deployment)**: nothing written. Needs systemd `.service`/`.timer` units for both
  jobs and a Hetzner runbook (CX22 sizing note, required env vars, one-time manual steps).
  Actual server provisioning is the user's action, not something to automate.

## Credentials / access needed from the user (not solvable by writing more code)

- **Brave Search API key** — required before M7 can do anything for real.
- **Hetzner account + server** — required before M10's deployment steps can be executed
  (the runbook can be written without it, but not run).
- **ntfy topic name** — trivial, just needs the user (or you) to pick a string.
- **`ANTHROPIC_API_KEY`** env var if you want `resolve/llm_extract.py`'s last-resort
  extraction step to actually run (install the optional `llm` extra: `pip install -e ".[llm]"`).
  This is the least urgent gap — steps 1-3 of the resolution pipeline should resolve most
  businesses before this ever triggers.

## Test suite map (56 tests, all passing on fixtures)

| File | Covers |
|---|---|
| `test_boliga.py` | Sharding, 300-cap bisection, truncation assertion, 403 handling, `discover_property_types` |
| `test_osm_extract.py` | pyosmium extraction, area computation, spatial queries against the synthetic fixture |
| `test_water.py` | Lake area filtering, eligibility signals (badevand/swimming_area/beach), strict vs. loose divergence |
| `test_bathing_water.py` | WFS-shaped GeoJSON schema validation, closed-station filtering, spatial site-to-lake matching, coastal sites excluded |
| `test_cvr.py`, `test_cvr_match.py` | CVR client parsing, quota/ban vs. not-found, fuzzy name matching, closed-business rejection |
| `test_jsonld.py` | LocalBusiness/PostalAddress extraction, `@graph`-wrapped JSON-LD, findsmiley link extraction |
| `test_address_regex.py` | Danish address regex, `NotConfiguredValidator` raising rather than accepting, `DatafordelerAddressValidator` (Gaeldende-status gate, postal-code cross-check, GraphQL error propagation) against a fake session |
| `test_resolve_pipeline.py` | All 4 resolution steps in order, and — the important one — a hallucinated LLM address being offered to the validator and dropped |
| `test_score_pipeline.py` | End-to-end scoring: water hard filter, OSM categories, business-directory wiring, sort order |

`tests/fixtures/sample.osm.xml` and `tests/fixtures/badevand_sample.geojson` are hand-built,
real-Sjælland-coordinates synthetic data (near Sorø) — useful reference for what a realistic
lake/coastline/marina layout looks like if you need more fixture data later.

## Suggested order for the next session

1. Run the pipeline for one real postal shard against live Boliga + the real OSM store + real
   CVR/address resolution; open the generated site and eyeball it against a real map. All
   five pieces (Boliga, OSM, bathing water, cvrapi.dk, DAR address validation) are now
   confirmed live individually — this is the first true end-to-end run.
2. M6 → M7 (get the Brave key first) → M8 → M9 remainder → M10.
