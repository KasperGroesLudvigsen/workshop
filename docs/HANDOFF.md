# Handoff — Summer House Screener

Read this first if you're picking this project back up.

## tl;dr

- Branch: `main` on `KasperGroesLudvigsen/workshop`.
- M0–M5 done, plus real hangout/grocery business discovery (a chunk of M6/M7's actual value,
  without needing OSM-POI extraction or Brave web-search). 62 tests pass.
- **2026-09-15**: closed all 5 verification gaps from the previous handoff using real internet
  access, then **replaced Boliga with Boligsiden** as the listings source entirely, after
  Boliga's Cloudflare protection proved unreliable for a real unattended run.
- **2026-09-16**: added real business discovery via Erhvervsstyrelsen's `cvr-permanent`
  system-til-system access (bulk enumeration by branch code + postal code — something
  cvrapi.dk, still used for name lookups, fundamentally can't do). Hangout/grocery are now
  *real* hard filters end to end, not just water. Details below.
- A real end-to-end run (`jobs/run_real.py`) works today: real fritidsbolig listings across
  the configured region, scored against the real OSM store, bathing-water data, and a real
  discovered business directory.
- M6 (findsmiley) and M7 (web-search gap-fill) remain for businesses *not* in CVR at all —
  a much smaller residual gap than before.
- M8–M10 not started.
- Full milestone detail: `docs/PLAN.md`.

## What got verified/fixed 2026-09-15

### 0. Fetch layer swapped: Boliga → Boligsiden — done

Boliga's API sits behind Cloudflare and needs `curl_cffi` browser impersonation to get past
at all — and even with that, a real `jobs/run_real.py` run hit repeated silent connection
timeouts right after the exact same calls had worked moments earlier. Not a code bug; the
data source itself is unreliable for an unattended job.

Researched two known Danish open-source projects: [Dan Saattrup Smart's
`bolig-ping`](https://github.com/saattrupdan/bolig-ping) queries **Boligsiden's own API**,
`api.boligsiden.dk/search/cases`, with a plain `requests.get()` — no headers, no
impersonation, confirmed live (clean 200s, no Cloudflare challenge, `per_page` up to 500
works). Mikkel Krogsholm's `api-mapper` turned out to be a generic DevTools network-recorder,
not a Boliga client — no reusable code there.

`src/screener/fetch/boligsiden.py` (replacing `fetch/boliga.py`, now deleted) implements
`BoligsidenClient`/`normalize_case`, improving on `bolig-ping`'s own implementation: loud
truncation detection (`BoligsidenTruncatedResultsError`) instead of trusting page-count
arithmetic, raw-response persistence to `db.py` (fetch/score separation), a
`BoligsidenBlockedError` for 403/429, and a shared `RateLimiter`
(`fetch/_rate_limit.py`, deduplicated out of `boliga.py`/`cvr.py`).

- `fritidsbolig_address_type: "holiday house"` — confirmed via the API's own 400 error body,
  which lists the full valid `addressTypes` enum.
- No server-side postal-*range* filter exists (only an exact-match `zipCodes` list) — the
  client fetches the whole national result set per `address_type` and filters by `zipCode`
  client-side against `config/thresholds.yaml`'s `postal_ranges`. No bisection needed either:
  Boligsiden has no small per-query cap the way Boliga did.
- Real fields used directly, no candidate-tuple guessing needed: `coordinates.lat/lon` (WGS84,
  top-level, no reprojection), `priceCash`, `housingArea`, `lotArea` (cleaner than any Boliga
  candidate we had), `numberOfRooms`, `yearBuilt`, `daysOnMarket`, `address.{roadName,
  houseNumber, zipCode, cityName}`.
- **Real-world pagination finding**: fetching all ~7,300 national "holiday house" cases across
  ~15 sequential requests (a few real seconds) hit exactly one case appearing on two
  consecutive pages — the live, offset-paginated dataset shifted slightly mid-crawl. Fixed by
  counting *unique* `caseID`s against `totalHits`, not raw item count; a duplicate is now
  logged and skipped rather than treated as truncation.
- `score/pipeline.py`'s output key `boliga_url` renamed to `listing_url`; the site template's
  "Boliga" link label is now "Listing", pointing at Boligsiden's stable redirect URL
  (`https://boligsiden.dk/viderestilling/{caseID}`, the same choice `bolig-ping` makes).
- `curl_cffi` dropped from `pyproject.toml` — nothing else used it.
- Verified end to end: `jobs/run_real.py` fetched 2,428 real listings across the full
  configured region (postal ranges 3000-3699 + 4000-4990) in one run, no blocking, no
  timeouts; 2,396 pass the water-only hard filter. Opened the generated site in a real browser
  — real pins clustered along the coast exactly where fritidshuse actually are.

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

### 6. CVR business discovery (`src/screener/fetch/cvr_discovery.py`, `resolve/cvr_discovery.py`) — done, 2026-09-16

Additive, not a replacement — `fetch/cvr.py` (cvrapi.dk) and `resolve/cvr_match.py` are
untouched. The user got free username/password access to Erhvervsstyrelsen's `cvr-permanent`
system-til-system solution — the authoritative CVR data, and critically, **bulk enumeration**
(list every business matching a branch code + postal code), which cvrapi.dk fundamentally
can't do (lookup-only). This closes the actual gap that's been blocking hangout/grocery from
being real hard filters since M5.

- Confirmed live: `http://distribution.virk.dk/cvr-permanent` — **plain HTTP, not HTTPS**
  (HTTPS timed out at the TCP level; the government system's own design, credentials go over
  the wire in cleartext, not fixable client-side). Basic Auth, Elasticsearch 6.8 Query DSL,
  free, near-real-time, full history including ceased companies.
- **Real finding**: DB07 branch codes were revised effective 2025-01-01. A code that looks
  right from a plain text search (old `"561010"` for restaurants) returns **zero** currently
  active businesses — every active company migrated to the new code (`"561110"`) when the
  revision landed; only historical/ceased companies still carry the old one. Every code in
  `config/thresholds.yaml`'s `cvr_branch_codes` was confirmed against currently-*active*
  companies specifically (`sammensatStatus: "aktiv"`, itself a `text` field needing the
  lowercased token, not `term`-matchable against the display string `"Aktiv"`).
- **Real finding**: CVR's own `adresseId` does **not** cross-reference DAR's current
  `id_lokalId` (tested against both `DAR_Adressepunkt` and `DAR_Husnummer` — empty both
  times, likely a legacy DAR-1.0→2.0 ID remap). So `resolve/cvr_discovery.py` builds an
  `AddressCandidate` from CVR's structured address fields and validates it through the
  existing `DatafordelerAddressValidator` unchanged, same as every other candidate source —
  CVR being authoritative for company data doesn't make its registered address trustworthy on
  its own (virtual offices, co-registered accountant addresses).
- `ice_cream` has no CVR discovery — the only plausible code (`"563010"`) is general
  non-alcoholic beverage service, not ice-cream-specific; using it would misclassify. Same gap
  as before, not a regression.
- Verified end to end for real (postal 4200 alone, to avoid hammering `DatafordelerAddressValidator`
  before it had a rate limiter): 43 real hangout candidates found, 35 validated (81%); 10
  grocery candidates, 6 validated; similar for wine_shop/butcher. Wired into `jobs/run_real.py`
  — hangout/grocery went from filtering nothing (every listing passed trivially) to real hard
  filters (36/81 listings passed all three in that test).
- **Done same day**: `DatafordelerAddressValidator` now takes a `requests_per_second` param
  (default 5.0, reusing the shared `fetch/_rate_limit.RateLimiter`) — it was unthrottled until
  bulk discovery gave it a usage pattern (thousands of calls per run) it wasn't originally
  sized for. Verified live (a real `validate()` call still resolves correctly with the limiter
  active) and with a fake-session test asserting two calls are actually spaced apart. A
  full-region discovery run is now safe to attempt, though still untested at that scale — see
  "Suggested order" below.

## Decisions already made (don't re-litigate without new information)

- **Stack**: Python, shapely 2.0 (STRtree, no separate `rtree` package needed) + pyproj +
  DuckDB + pyosmium + Jinja2. Listings fetch uses plain `requests` (Boligsiden needs no
  browser impersonation, unlike Boliga previously).
- **CVR access**: cvrapi.dk (free, no registration) for name→address lookups (step 1 of
  `resolve/pipeline.py`), **plus** Erhvervsstyrelsen's `cvr-permanent` system-til-system
  access (also free, a registered username/password) for bulk business *discovery* by branch
  code + postal code, which cvrapi.dk can't do at all. Deliberately additive, not consolidated
  onto one CVR source yet — see item 6 above.
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
  API key** (not yet obtained — get one before starting this). Now a smaller residual gap
  than before item 6 above: CVR discovery covers every *registered* business in a category;
  this is only for businesses missing from CVR entirely (informal/seasonal food stalls,
  etc). Search per town, not per listing; feed discovered names through the *existing*
  `resolve/pipeline.py` unchanged — never skip the address-register gate just because a name
  came from search.
- **M8 (nightly orchestration)**: `jobs/nightly.py` doesn't exist. Should tie together fetch
  (M1) → score (M3-M7) → diff against yesterday (use `db.py`'s `save_scored_listings` and
  `passing_listing_ids`, already implemented) → notify via ntfy on new passes.
- **M9 (site polish), remainder**: the template (`site/templates/index.html.j2`) already has
  live typed-input filtering for price/rooms/m²/lot/build-year/the three thresholds/lake-
  strict, all client-side with no page reload — verified working via headless browser render.
  Still missing: per-category **minimum-count** filter inputs ("at least 3 hangouts within
  threshold"), and table columns for every field per the brief ("all columns visible") —
  address, energy class, days on market, and three of the four CVR categories now have real
  discovered data (fish_shop, wine_shop, butcher — ice_cream still has none, see item 6) but
  aren't columns yet.
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

## Test suite map (62 tests, all passing on fixtures)

| File | Covers |
|---|---|
| `test_boligsiden.py` | Pagination + client-side postal-range filtering, truncation assertion, duplicate-`caseID` handling, 403/429 handling, field mapping |
| `test_cvr_discovery.py` | cvr-permanent query shape (branch-code/postal-range `should` clauses, status filter), pagination, result-window/blocked-status handling, and the resolve-layer mapping (validated vs. dropped hits, missing-field skip) |
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

Hangout/grocery are now real hard filters via CVR discovery (see item 6 above), verified for
one postal code — but the full configured region hasn't been run with discovery wired in yet.
`DatafordelerAddressValidator` is now rate-limited (5 req/sec default), so this should be
safe to attempt, just not yet actually run at that scale — likely still slow (thousands of
businesses × up to 2 calls each), so consider a simple on-disk cache keyed by candidate
address too, since the same street/postal/town recurs a lot across CVR hits.

1. Run `jobs/run_real.py` for the full region with discovery wired in for real, and see how
   long it actually takes at 5 req/sec before deciding whether caching is worth adding.
2. M6 (findsmiley) and/or M7 (web-search gap-fill, needs a Brave key) — for businesses missing
   from CVR entirely, now a smaller residual gap. Either produces candidate names that feed
   the *already-built* `resolve/pipeline.py` unchanged.
3. M8 (nightly orchestration) → M9 remainder (including the fish_shop/wine_shop/butcher table
   columns discovery now populates) → M10.
