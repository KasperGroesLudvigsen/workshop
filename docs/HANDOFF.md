# Handoff — Summer House Screener

Read this first if you're picking this project back up.

## tl;dr

- Branch: `main` on `KasperGroesLudvigsen/workshop`.
- M0–M5 and M7 (business discovery beyond CVR) done. 105 tests pass.
- **2026-09-21**: CVR discovery and OSM POI extraction — both region-wide and
  listing-independent, together the bulk of a real run's ~30 minutes — are now cached to disk
  (`data/cvr_business_cache.pkl`, `data/osm_poi_cache.pkl`) and only rebuilt when actually
  stale, so running `jobs/run_real.py` a few times a week (to pick up new listings) doesn't
  redo either pass every single time. See item 10 below.
- **2026-09-15**: closed all 5 verification gaps from the previous handoff using real internet
  access, then **replaced Boliga with Boligsiden** as the listings source entirely, after
  Boliga's Cloudflare protection proved unreliable for a real unattended run.
- **2026-09-16**: added real business discovery via Erhvervsstyrelsen's `cvr-permanent`
  system-til-system access (bulk enumeration by branch code + postal code — something
  cvrapi.dk, still used for name lookups, fundamentally can't do). Hangout/grocery are now
  *real* hard filters end to end, not just water. Details below.
- **2026-09-19**: user spot-checked real Bisserup businesses missing from discovery, leading
  to two real fixes — a postal-code parser bug affecting *every* address validation call in
  the project, and a whole missing CVR index (`produktionsenhed`) for chain/cooperative
  store locations — plus a genuine structural blind spot (a business run through a
  property-holding company registered elsewhere) that no CVR fix can close. See item 7 below.
- **2026-09-19 (same day)**: built M7 — OSM POI discovery (primary, no address-validation
  gate needed) plus a Tavily web-search fallback (free, no card, unlike the
  originally-assumed Brave Search API which lost its free tier). OSM POI extraction confirmed
  against the real local `data/denmark-latest.osm.pbf`: both Bisserup test cases found, plus
  a bonus hit.
- **2026-09-20**: finished M7 — added the gap-detection/orchestration layer that decides
  *which* (town, category) pairs actually need a Tavily search (nothing called
  `discover_via_web_search` before this), a real page fetcher, a real read-through cache
  (the `raw_responses` table's `get_cached_response` had existed since the start but was
  never actually read by any fetch client until now), and a hard monthly credit budget plus
  a per-run pacing cap. Verified against the real Tavily API for real (Bisserup, postal 4243)
  — see item 9 below for the two real findings from that run and the current residual
  limitation. M7 is now fully done.
- A real end-to-end run (`jobs/run_real.py`) works today: real fritidsbolig listings across
  the configured region, scored against the real OSM store, bathing-water data, and a real
  discovered business directory (CVR + OSM POIs + Tavily gap-fill).
- M6 (findsmiley) is now the only real residual gap.
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
  active) and with a fake-session test asserting two calls are actually spaced apart.

### 7. Two real bugs found by spot-checking known Bisserup businesses — done, 2026-09-19

The user knew Bisserup has a supermarket, a kro, and an ice cream/grill place, none of which
showed up as discovered businesses. Investigating all three against live `cvr-permanent`/DAR
found two distinct, fixable bugs, plus one thing that genuinely isn't fixable on our end:

- **Bug A (`resolve/address_regex.py`'s `_parse_postal_from_betegnelse`), the bigger one**:
  broke on any DAR address with a "supplerende bynavn" (a hamlet/village name DAR inserts
  between the street and the postal code — very common for small places within a larger
  postal town, e.g. `"Bisserup Byvej 3, Bisserup, 4243 Rude"`). Taking the *first*
  comma-separated segment mis-parsed the bynavn itself as the postal code, failed
  `isdigit()`, and silently dropped an otherwise perfectly valid, Gaeldende address. Fixed by
  taking the *last* segment instead. This gate is used by every address validation in the
  project (both `resolve/pipeline.py`'s cvrapi.dk lookups and every discovery hit), so this
  one-line fix likely recovers real businesses region-wide, not just in Bisserup.
- **Bug B (`fetch/cvr_discovery.py`)**: only queried the CVR `virksomhed` (company) index.
  The real grocery store is registered as `Brugsen Holsteinborg`, branch code `471120`
  (already in our mapping, unchanged), status `Aktiv` — but as a **`produktionsenhed`**
  (physical branch/P-unit), a separate index never queried before. Normal pattern for
  chain/cooperative stores: the legal company can be registered anywhere, but each physical
  branch is its own P-unit with its own real address. `discover_active_businesses` now
  queries both indices (produktionsenhed's field paths differ:
  `VrproduktionsEnhed.produktionsEnhedMetadata.*`, confirmed live via `_mapping`) and merges
  them, deduping by physical address since a small single-location business's own P-unit is
  often at the identical address as its `virksomhed` entry.
- **Correction, 2026-09-19 (same day)**: the "Ophørt" claim above was wrong. It came from a
  name-based CVR search that matched a different, genuinely-closed historical registration
  sharing a similar name — not the kro's real current operator. The user provided a real
  receipt (`BISSERUP STRAND KRO, BISSERUP HAVNEVEJ 67, 4243 RUDE, CVR-nr. 35819894`); looking
  that CVR number up directly shows status `NORMAL` (active), registered to
  **`EJENDOMSSELSKABET BSK ApS`**, a property-holding company registered in Borup — a
  different town — under a property-administration branch code, not a restaurant one. This is
  a genuine, structural blind spot no amount of CVR fixing can close: branch-code discovery
  has no way to know a property-holding company in one town operates a restaurant in another.
  See item 8 below for the fix (OSM POI discovery, which doesn't go through CVR at all). Lesson:
  verify a specific claim against the actual entity (CVR number, receipt, etc), not a name
  search that can silently match the wrong record.
- Verified live end to end both ways: `validate()` on the real address returned `None` before
  Bug A's fix, a real `ValidatedAddress` after; `discover_active_businesses` found zero
  grocery candidates in postal 4243 before Bug B's fix, `Brugsen Holsteinborg` after. Full
  pipeline (discover → resolve → validate) confirmed producing a real `ResolvedBusiness` for
  it. Not yet re-run at full-region scale — see "Suggested order" below.

### 8. M7: business discovery beyond CVR (`fetch/osm_poi.py`, `fetch/web_search.py`, `resolve/web_discovery.py`) — mostly done, 2026-09-19

Implements the plan at (formerly) `giggly-weaving-dawn.md`, test-driven against the two real
Bisserup businesses from item 7's correction. Two independent mechanisms, primary + fallback,
neither using the originally-assumed Brave Search API (it dropped its free tier Feb 2026 —
card required, then metered billing). Researched free alternatives broadly first: DuckDuckGo
Instant Answer isn't general search; Google Custom Search is closed to new signups; Bing
Search API was retired by Microsoft Aug 2025; SearXNG needs self-hosting for reliability;
most others require a card. Tavily is the one genuinely free option (1,000 credits/month,
recurring, no card).

- **Primary: `fetch/osm_poi.py`** — a separate `osmium.SimpleHandler` (deliberately not
  touching the existing, tested `OsmHandler` in `osm_extract.py`) that pulls named
  nodes/ways tagged `amenity=restaurant/fast_food/cafe/bar/pub/ice_cream` or
  `shop=supermarket/convenience/grocery/seafood/alcohol/wine/confectionery/butcher` straight
  into `ResolvedBusiness` records with `source_step="osm_poi"` and **no address-validation
  gate** — intentional, not a shortcut: OSM POIs are already-placed real geometry from a
  structured dataset (the same trust level `marina`/`playground`/`beach` already get from the
  same PBF), not raw text being geocoded. Confirmed live against the public Overpass API
  *before* writing this module: querying named amenities near Bisserup immediately returned
  `Bisserup Strand Kro` (`amenity=restaurant`) and `Bisserup Is og Grillhus`
  (`amenity=fast_food`) by their real names with exact coordinates — plus two businesses we
  didn't even know to look for (`Bisserup Fiskebar`, `Bisserup Camping Kiosken`) and the
  grocery store under its real brand name `Dagli'Brugsen`, not the legal cooperative name CVR
  uses. 6 unit tests against a synthetic fixture (`tests/fixtures/business_pois.osm.xml`,
  built from the real confirmed-live Overpass coordinates) all pass — node extraction, way
  centroid computation, unnamed/irrelevant-tag skipping. Wired into
  `jobs/run_real.py`'s `_discover_business_directory`: runs after CVR discovery, results
  concatenated into `businesses_by_category` per category (no cross-source dedup with CVR
  yet — a cosmetic duplicate risk, e.g. `Dagli'Brugsen` vs. `Brugsen Holsteinborg` both under
  `grocery`, not a correctness bug). **Confirmed against the real local
  `data/denmark-latest.osm.pbf`, 2026-09-19**: found both `Bisserup Strand Kro`
  (55.1996228, 11.4932746) and `Bisserup Is og Grillhus` (55.199526, 11.494104), matching the
  earlier live-Overpass coordinates almost exactly, plus a bonus hit (`Bisserup Fiskebar`) —
  region-wide totals: 14,976 hangout / 3,884 grocery / 883 butcher / 808 ice_cream /
  677 wine_shop / 149 fish_shop POIs. Takes **~20 minutes** for the full pass (a second,
  separate osmium pass over the whole Denmark PBF, on top of `osm_extract.py`'s own pass) —
  fine for a one-shot `run_real.py` run today, but worth remembering if this ever needs to
  run more often than nightly.
- **Fallback: `fetch/web_search.py` (`TavilyClient`) + `resolve/web_discovery.py`** — for
  whatever OSM genuinely doesn't have tagged. `TavilyClient.search(query)` hits
  `POST https://api.tavily.com/search` with `Authorization: Bearer <key>`, same
  raise-if-unset env-var convention as every other credential here
  (`TAVILY_API_KEY`), rate-limited, raw responses persisted via `db.py`. `web_discovery.py`
  builds a per-category Danish query from the new `web_search_terms` config
  (`config/thresholds.yaml`, parallel to `cvr_branch_codes`), and for each result runs the
  *same* JSON-LD → regex → LLM cascade `resolve/pipeline.py` already uses, gated by the same
  `AddressValidator` — unlike OSM POIs, a search result's page really is raw text that could
  be wrong.

### 9. M7 finished for real: gap-detection orchestration, real cache, budget, and the real-API findings — done, 2026-09-20

Everything in item 8 above was built and unit-tested, but nothing ever actually called it —
there was no code anywhere deciding *which* town/category needed a search, and no real
`PageFetcher` implementation for it to fetch a search result's own page with (only `None` or
test fakes existed). Closed all of that:

- **`resolve/web_discovery_gaps.py`** (new): `find_gaps` reuses `geo.amenities.category_summary`
  (the exact same nearest-distance helper scoring itself uses) against the CVR+OSM-only
  directory to decide, per listing town, whether a category has *no* candidate within
  `amenity_search_radius_km` (15km, reused rather than adding a new threshold) — including
  categories the directory doesn't have at all (`ice_cream`, no CVR code). Deduped by
  (town, category). `prioritize_gaps` puts `hangout`/`grocery` (the actual hard filters) ahead
  of the four informational categories. `fill_gaps` calls `discover_via_web_search` per gap,
  stopping gracefully (not crashing) on either the monthly budget or a real Tavily block.
- **`fetch/page_fetch.py`** (new): the first real `PageFetcher` implementation in the codebase
  — plain `requests.get`, returns `None` (never raises) on any failure.
- **The `raw_responses` cache is now actually a read-through cache for Tavily**: `db.py`'s
  `get_cached_response` has existed since M0 but no fetch client ever called it — every
  client only ever *wrote* to this table. `TavilyClient.search` now checks it first; a hit
  replays the original response (including a previously-blocked one, so it fails the same way
  twice, not silently) with **no network call and no row rewrite**, so `fetched_at` never
  advances and monthly budget accounting stays correct. `web_search_monthly_budget` (900) and
  `web_search_max_calls_per_run` (150) are both new `config/thresholds.yaml` values, enforced
  inside `TavilyClient` (budget, checked before every non-cached call) and in `fill_gaps`
  (per-run cap, tracked via the new `TavilyClient.calls_made` counter which only increments on
  genuine network calls).
- **Real bug found and fixed while verifying this live**: `resolve/llm_extract.py`'s
  `anthropic_extractor` raised a raw `ModuleNotFoundError` when the optional `llm` extra
  wasn't installed — `extract_candidate`'s except clause only caught `RuntimeError`, so the
  whole gap-fill pass crashed the first time the JSON-LD→regex cascade actually fell through
  to step 4 for real (never exercised before now — every existing test always injected a fake
  `llm_extractor`). Fixed by wrapping the lazy `import anthropic` and re-raising as
  `RuntimeError`, so "extra not installed" degrades exactly like "API key not set" already did
  — no candidate, not a crash.
- **Real finding, not (yet) fixed — query quality for small hamlets**: verified live against
  postal 4243 (Bisserup). Boligsiden's own `cityName` field (now captured as `listing["town"]`
  in `fetch/boligsiden.py`'s `normalize_case`) is the *postal town* ("Rude"), not the actual
  local hamlet ("Bisserup" is a `supplerende bynavn` within it — the exact same DAR field that
  caused item 7's Bug A). Worse, "Rude" happens to also be an ordinary English word, so the
  first live search returned unrelated US results (a Dallas café, a Texas restaurant review).
  **Mitigated**: `resolve/web_discovery.py`'s `build_query` now appends `"Danmark"` to every
  query — confirmed live this alone was enough to make the *same* "Rude"-based search return
  genuinely Bisserup-relevant results (`bisserupstrandkro.dk`, `Bisserup_Strand_Kro` on
  TripAdvisor, `bisserup-fisk` on Kompass). The underlying postal-town-vs-hamlet granularity
  gap is **not** fixed — Boligsiden simply doesn't expose the finer hamlet name, and getting it
  would need a real reverse-geocode (e.g. nearest OSM `place=hamlet/village` node, not
  currently extracted into `GeometryStore` at all) — not attempted, out of scope for this pass.
- **Real finding, not fixed, deliberately not chased further — page-fetch blocking**: every
  single search-result page fetched during the live verification run (Trustpilot, TripAdvisor,
  Kompass, Yelp, even the business's own `bisserupstrandkro.dk`) returned HTTP 403 to
  `fetch/page_fetch.py`'s plain `requests.get`. This is the same class of problem that made
  Boliga's Cloudflare protection unreliable enough to drop entirely (see the top of this file)
  — except here it's hitting a tertiary, best-effort fallback, not the primary listings source,
  so the fix isn't "switch data source", it's "accept some real businesses won't be
  extractable via this path". Deliberately **not** pursuing browser-impersonation
  (`curl_cffi`-style TLS/fingerprint spoofing) to get past this — that's meaningfully different
  from a plain unrealistic User-Agent, and this fallback's whole design already assumes a
  real, non-trivial miss rate (the JSON-LD → regex → LLM cascade already tolerates individual
  page failures without crashing). Net result of the live run: gap-detection, real Tavily
  calls, and the persistent cache (confirmed: an identical re-run made **zero** new real
  calls) all verified working end to end; zero businesses were actually resolved in this
  particular narrow sample because every result page happened to be blocked. A future session
  could try a more realistic (but still honest, non-impersonating) `User-Agent` string as a
  mild, low-risk mitigation if this turns out to matter at full-region scale.

### 10. Discovery caching: CVR + OSM POI no longer redone on every run — done, 2026-09-21

The user will be re-running `jobs/run_real.py` a few times a week to pick up fresh listings.
CVR bulk discovery and OSM POI extraction are both region-wide, not listing-scoped — they had
no reason to be redone every time, but were (each `_search`/pyosmium pass only ever *wrote* to
`db.py`'s `raw_responses` cache, exactly the write-only gap M7's Tavily cache had before item 9
fixed it). Closed with a small generic helper, **`fetch/discovery_cache.py`**'s `load_or_build`
(pickle-backed, mirrors `geo/store.py`'s existing `GeometryStore.save`/`.load` convention),
wrapping both stages' *output* (the fully resolved `dict[str, list[ResolvedBusiness]]`, not
individual HTTP pages) so a cache hit skips Elasticsearch queries, DAR validation calls, and
the pyosmium pass alike. Two different staleness signals: OSM POI is stale only when
`denmark-latest.osm.pbf`'s mtime is newer than the cache (mirrors how `osm_store.pkl` already
works — build once, reuse until you re-download); CVR discovery has no local source file to
check, so it uses a configurable max-age (`cvr_cache_max_age_days`, default 7). The existing
inline CVR discovery loop in `jobs/run_real.py` was extracted into a proper, directly-tested
function, `resolve/cvr_discovery.py`'s `discover_and_resolve_all_categories` (pure refactor, no
behavior change). New `--osm-poi-cache`/`--cvr-cache`/`--rebuild-discovery-cache` CLI args on
`jobs/run_real.py`. 10 new/extended tests (`tests/test_discovery_cache.py`,
`tests/test_cvr_discovery.py`).

**Confirmed live, real numbers** (postal 4243, full-region CVR/OSM scope since those stages
aren't listing-scoped): first call (cold cache, real CVR discovery + DAR validation + real OSM
POI extraction) took **2,132.8s (~35.5 min)**; an immediate second call with both caches warm
took **0.6s** — hangout/grocery layer point counts identical between the two (16,328/4,161),
confirming the cache round-trips correctly, not just quickly.

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
- **M7 (business discovery beyond CVR) — done, see items 8-9 above**: OSM POI discovery
  (`fetch/osm_poi.py`) and the Tavily web-search fallback (`fetch/web_search.py`,
  `resolve/web_discovery.py`, `resolve/web_discovery_gaps.py`) are both wired into
  `run_real.py` and confirmed against real data/APIs. Residual, deliberately-not-chased
  limitation: many real business pages block a plain HTTP fetch (item 9's page-fetch-blocking
  finding) — a lower discovery yield for this fallback specifically, not a crash risk.
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

- **Hetzner account + server** — required before M10's deployment steps can be executed
  (the runbook can be written without it, but not run).
- **ntfy topic name** — trivial, just needs the user (or you) to pick a string.
- **`ANTHROPIC_API_KEY`** env var if you want `resolve/llm_extract.py`'s last-resort
  extraction step to actually run (install the optional `llm` extra: `pip install -e ".[llm]"`).
  This is the least urgent gap — steps 1-3 of the resolution pipeline should resolve most
  businesses before this ever triggers.

## Test suite map (105 tests, all passing on fixtures)

| File | Covers |
|---|---|
| `test_boligsiden.py` | Pagination + client-side postal-range filtering, truncation assertion, duplicate-`caseID` handling, 403/429 handling, field mapping |
| `test_cvr_discovery.py` | cvr-permanent query shape for *both* `virksomhed`/`produktionsenhed` indices, pagination, cross-index dedup by physical address, result-window/blocked-status handling, the resolve-layer mapping (validated vs. dropped hits, missing-field skip), and `discover_and_resolve_all_categories` combining per-category results end to end |
| `test_osm_extract.py` | pyosmium extraction, area computation, spatial queries against the synthetic fixture |
| `test_osm_poi.py` | OSM POI tag→category mapping, node + way(centroid) extraction, unnamed/irrelevant-tag skipping, against `tests/fixtures/business_pois.osm.xml` (real Bisserup coordinates) |
| `test_web_search.py` | `TavilyClient` request shape (Bearer auth, query body), result parsing, blocked-status handling, missing-key error, the read-through cache (hit skips transport and never rewrites the row, a cached blocked response replays as blocked), monthly-budget enforcement, `calls_made` only counting real network calls |
| `test_web_discovery.py` | Per-town/category query building (incl. the "Danmark" disambiguation suffix), the JSON-LD → regex → LLM cascade against fake search results and pages, unvalidated-candidate dropping, page-fetch-failure handling |
| `test_web_discovery_gaps.py` | Gap detection (missing/too-far category, dedup by town+category, listings without a town), hard-filter-category prioritization, `fill_gaps` stopping gracefully on the per-run cap / monthly budget / a real block |
| `test_discovery_cache.py` | `save_cache`/`load_cache` round-trip, `is_stale` (missing cache, source newer/older, max-age exceeded/not), `load_or_build` (cache hit never calls the real build function, cache miss builds+persists, `force_rebuild` bypasses a fresh cache) |
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

`jobs/run_real.py` has been run at full-region scale with discovery wired in (2026-09-19:
2,407 real listings, 1,311 passing all hard filters; ~10 min end to end at 5 req/sec — fast
enough that a validation-result cache hasn't been needed yet). That run predates item 7's two
bug fixes above, though.

1. Re-run `jobs/run_real.py` for the full region now that item 7's fixes and items 8-9's OSM
   POI + Tavily gap-fill wiring are all in, and compare hangout/grocery candidate/validation
   counts against the 2026-09-19 numbers (945/108 candidates, 529/65 validated) — expect both
   CVR counts to hold, OSM-sourced entries (`source_step == "osm_poi"`) to add
   previously-invisible businesses like the Bisserup kro, and a `web_discovery_gaps: N gap(s)
   found` log line reporting how many (town, category) pairs needed the Tavily fallback at
   full region scale. Watch `data/screener.duckdb`'s `raw_responses` row count for
   `tavily_search` against `web_search_monthly_budget` (900) the first time this runs at full
   scale with a cold cache.
2. M6 (findsmiley) — feeds off the same `resolve/jsonld.py` findsmiley-link extraction M7's
   web-search path also uses.
3. Optional, only if the full-region run shows the item 9 page-fetch-blocking finding is
   costing real discovery yield: try a more realistic (but still honest) `User-Agent` in
   `fetch/page_fetch.py` as a mild mitigation — deliberately not attempted yet, see item 9.
4. M8 (nightly orchestration) → M9 remainder (including the fish_shop/wine_shop/butcher table
   columns discovery now populates) → M10.
