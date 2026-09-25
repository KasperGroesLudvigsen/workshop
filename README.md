# Summer House Screener

A filter, not a recommender. It pulls holiday-home ("fritidsbolig") listings from Boligsiden
across Sjælland, Lolland, Falster, and Møn, drops everything that fails a small set of hard
geographic requirements, and shows what survives on a single static, interactive map + table
page — with live-adjustable thresholds, so tightening or loosening a requirement never means
re-scraping or re-running anything.

## What it does

You give it three hard requirements, each a distance in kilometres:

- **Open water** — the sea, or a lake that's actually swimmable (see "Lake eligibility" below).
- **A hangout** — somewhere to eat/drink nearby (a "kro", café, restaurant, etc).
- **A grocery store** — for basic provisioning.

Every listing is checked against all three. Anything that fails even one is dropped from the
result set entirely (a hard filter, not a ranking penalty). What's left is shown on a map with
one pin per listing, and in a sortable table alongside it, side by side.

### Lake eligibility

The sea always counts as open water. A lake only counts if it's swimmable, judged by three
independent signals, any one of which is enough:

1. **Officially designated bathing water** — the lake has a real, government-registered
   bathing site within 200m of its shore (see "Data sources" below).
2. **OSM `leisure=swimming_area`** — OpenStreetMap already tags it as a swimming area.
3. **A beach on its shore** — `natural=beach` touching the lake.

Lakes smaller than 5 hectares never count, regardless of signal — farm ponds and drainage
basins carry the same OSM tags as real lakes. Both a *strict* eligibility mode (requires one
of the three signals) and a *loose* mode (any nearby lake counts, signal or not) are always
computed for every listing, so the strict/loose toggle in the UI is instant — it never
triggers a re-score.

### How the front end is built

There's no frontend build step and no backend server-side logic — `src/screener/site/build.py`
renders one self-contained `data/site/index.html` from a single Jinja2 template
(`site/templates/index.html.j2`), with the full list of already-scored listings and the
threshold config both embedded directly as JSON in a `<script>` block. Everything after that is
plain, dependency-free JavaScript in that same file (no React/Vue, no bundler, no npm install)
plus [Leaflet](https://leafletjs.com) pulled from a CDN for the map.

All filtering, sorting, and threshold-driven recomputation happen **client-side, against the
embedded JSON** — moving a threshold slider re-runs a single `render()` function that
re-derives every row's pass/fail and distances from the raw scored data and redraws both the
map markers and the table, with no server round-trip and no re-fetch. This is what makes the
live threshold controls instant: every distance/candidate-list the UI could ever need was
already computed once by the Python scorer and shipped in the page, so the JS never has to
re-score anything, only re-filter/re-sort numbers already in memory. Liked/hidden state is the
one piece of real client-side state, kept in `localStorage` (see "Liking and hiding listings"
below) rather than sent anywhere, since there's nowhere to send it to.

## What it shows

**The map** (Leaflet + OpenStreetMap tiles): one marker per surviving listing. Clicking a pin
pops up its address, price/size/rooms, a photo (when one could be found), open-water breakdown
(sea vs. lake, with the eligibility reason), flood risk (see below), and the named
hangouts/grocery stores/marinas/playgrounds/pools/beaches found nearby.

**The table**, one row per listing, every column sortable by click:

| Column | What it is |
|---|---|
| Open water / Sea / Lake (km) | Distance to the nearest open water, and the sea/lake breakdown |
| Lake signal | Why the nearest lake counts as swimmable (`badevand`, `swimming_area`, `beach_on_shore`, or `none`) |
| Hangout (km) / Hangouts within | Distance to the nearest hangout, and how many fall within the current threshold |
| Grocery (km) | Distance to the nearest grocery store |
| Marina / Playground / Pool / Beach (km) | Informational-only distances — shown, never filtered on |
| Price, m², Lot m², Rooms, Baths, Year | Listing basics, straight from Boligsiden |
| Flood risk | Coastal storm-surge flood risk, today and projected to 2120 — see "Flood risk" below |
| Drive time | Driving time/distance from a configurable point of departure — see "Driving time" below |
| Links | The Boligsiden listing (both the agent's own page and a boligsiden.dk address page), Google Maps pin/aerial/street view |

### Flood risk

Each listing shows a coastal (storm-surge) flood-risk reading, e.g. **"≤50yr storm surge
(1.16m)"**, sourced from Kystdirektoratet's official **Kystplanlægger 2120** model — free, public
data, not scraped from anywhere (see "Why not DinGeo" below).

- **"≤50yr"** is a *return period*: the shortest/most frequent storm severity Kystdirektoratet's
  model expects would flood this specific point, out of the four severities it tests (50, 100,
  1,000, and 10,000 years). A **lower** number means a **more common** storm already floods the
  spot — worse — since a "50-year storm" happens far more often than a "10,000-year storm" (this
  isn't a countdown or a guarantee of exact timing, just the model's long-run average frequency
  for a storm of that severity).
- **"(1.16m)"** is the modelled flood *depth* in metres at that same storm severity — how deep
  the water is expected to get there, not how far it reaches inland.
- **"today" vs. "by 2120"** are two separate readings for the same location: current conditions,
  and Kystdirektoratet's own projection incorporating expected sea-level rise. The 2120 figure is
  usually worse (a shorter return period and/or greater depth) for exactly that reason — climate
  change, not a data error.
- **"no flood risk mapped"** means none of the four tested storm severities produced a hazard hit
  at that point *in this model* — not a certified "flood-proof" guarantee, just the best reading
  this specific dataset can honestly give. See the note below on why this is a two-state reading,
  not three.

**Why not DinGeo (or a simpler "is this an official risk area" flag)?** DinGeo.dk shows a similar
number, but both its `robots.txt` and terms of service explicitly forbid scraping/automated
access, even for personal, non-commercial use — so its data isn't reused here at all. An earlier
version of this feature instead queried Kystdirektoratet's official *EU Floods Directive*
risk-area mapping (a boolean "is this one of ~51 officially designated risk-area municipalities"
flag) — confirmed live to leave ~90% of listings "not assessed", since most summer houses sit in
small coastal hamlets that regulatory dataset was never scoped to cover. Kystplanlægger 2120 is
Kystdirektoratet's own *broader* nationwide coastal-risk screening tool instead, covering the
whole Danish coast rather than a short list of cities — confirmed live against real listings that
came back "not assessed" under the old approach, which came back with real hazard/depth data
under this one.

That broader coverage comes with one honest tradeoff: this model can't reliably tell apart "a
real coastal point Kystdirektoratet checked and found safe" from "a point outside the model
entirely" — both come back with no hazard data. The old, narrower dataset *could* draw that line
(it had a dedicated designated-risk-area boundary layer), but this one doesn't have an equivalent.
Rather than falsely imply a location was checked and cleared, "no flood risk mapped" is used for
both cases.

### Driving time

Each listing shows driving time and distance from a single, configurable point of departure, e.g.
**"38min (35km)"** — set via `DRIVING_ORIGIN_ADDRESS` in `.env` (see "Set up API keys and
configuration" above). The configured address is also shown in the page header, so it's always
clear what the numbers are measured from.

Computed via [OSRM](https://project-osrm.org)'s free, public routing server, using its **Table**
service — a single request computes driving time/distance from one origin to many destinations at
once (confirmed live: 100 destinations per request, well within the server's own limits), rather
than one request per listing. For the ~2,400 listings in a full regional run, that's on the order
of 25 requests total, rate-limited to match OSRM's own published usage policy ("reasonable,
non-commercial use... must not exceed 1 request per second"). Each listing's own result is still
cached individually (like `flood_risk` and the listing photo), so a later run only fetches
listings that are actually new — changing `DRIVING_ORIGIN_ADDRESS` naturally invalidates old
results too, since the origin is part of the cache key, with no manual cache-clearing step needed.
**"no route found"** means OSRM couldn't compute a route at all (e.g. an island with no bridge or
ferry link in the road network it uses), not that the listing is unreachable in reality.

**Live filter controls** above the map/table recompute both the map and the table instantly,
client-side, with no server round-trip and no re-score:
- The three hard-filter distances (water/hangout/grocery), each independently adjustable
- The strict/loose lake-eligibility toggle
- Price range, minimum rooms, minimum m², minimum lot size, minimum build year
- "Show only liked" and "Show hidden" — see "Liking and hiding listings" below

### Liking and hiding listings

Each row (and map popup) has a ★ **Like** and a 🙈 **Hide** button. Liked listings get a
highlight and can be filtered to on their own ("Show only liked"). Hidden listings drop out
of the table and map by default — not deleted, just out of the way — and reappear (dimmed)
when "Show hidden" is checked. This is stored in your browser's `localStorage`, not on disk
or in the data itself, so it's tied to whatever URL/path you open the site from — see
"Reopening the app" below for why that matters.

## How it's built

**Fetch and score are deliberately separate stages.** Fetching (Boligsiden listings, the OSM
extract, the bathing-water register, CVR business lookups) only ever writes raw data to disk;
scoring only ever reads from that persisted raw data. Changing a threshold in
`config/thresholds.yaml` and re-running the scorer reproduces a new result set from the same
inputs in under a second — it never re-scrapes anything.

**Business resolution is a fixed pipeline, not a search loop.** Turning a business *name*
(found via a website, OSM, or web search) into a *validated, address-register-confirmed*
location goes through up to four steps in order — a CVR fuzzy name match, JSON-LD structured
data from the business's own site, a regex address candidate from page text, and an LLM
extraction as a last resort — stopping at the first one that validates. Nothing is ever
geocoded from raw, unvalidated text, including LLM output: every candidate, regardless of
which step produced it, passes through the same real address-register lookup before it's
trusted.

### Data flow: from a Boligsiden listing to the display page

1. **Fetch** — `BoligsidenClient` pulls raw fritidsbolig cases from the Boligsiden API for the
   configured postal ranges, and `normalize_case` turns each one into the listing shape the
   rest of the pipeline expects (address, price, m², coordinates, etc).
2. **Build the business directory** — independently of any one listing, hangout/grocery/etc.
   businesses are discovered once for the whole region: `CvrPermanentClient` enumerates
   candidates from CVR by industry code, each is resolved to a validated, address-register-confirmed
   location (see "Business resolution" above), and OSM business POIs are extracted and merged
   in alongside them into a single `business_directory`.

   *How OSM businesses get matched to a listing.* Extraction and per-listing matching are two
   separate steps, and neither is a postal-code filter or a radius-bounded walk of the map data:
   - **Extraction is nationwide and listing-agnostic.** `extract_business_pois` (`fetch/osm_poi.py`)
     makes one pyosmium pass over the *entire* Denmark `.osm.pbf` extract and pulls out every
     named node/way anywhere in the country whose `amenity`/`shop` tag matches a known category
     (e.g. `amenity=restaurant` → `hangout`, `shop=supermarket` → `grocery`) — with no notion of
     any listing, postal code, or distance yet.
   - **Indexing.** `build_business_directory` (`geo/business_directory.py`) turns each category's
     nationwide point list into a `Layer` (`geo/store.py`): points are projected into a metric CRS
     and loaded into a `shapely` `STRtree` (an R-tree spatial index) — one index per category,
     covering all of Denmark.
   - **Per-listing lookup happens only at query time**, against that pre-built index:
     `Layer.nearest(point)` returns the closest business in a category and its distance;
     `Layer.within(point, radius_m)` returns every business within the current threshold by first
     using the STRtree to fetch candidates inside a bounding buffer, then exact-filtering that
     small candidate set by real distance.

   In short: retrieve everything once, index it, then query the index per listing — the distance
   threshold only ever applies at query time against the index, never during extraction.
3. **Score** — `score_listings` takes the raw listings, the OSM geometry store (coastline,
   lakes, beaches, marinas, ...), the bathing-water lookup, and the business directory, and for
   each listing computes distances to the nearest open water (with the sea/lake/eligibility
   breakdown), hangout, grocery store, and the informational-only categories (marina,
   playground, pool, beach) — plus a `passed` flag from the three hard filters in
   `config/thresholds.yaml`. This is the "enrichment" step: a listing goes in with just its
   Boligsiden fields and comes out with every distance/eligibility field the map and table need.
4. **Sort** — `sort_scored_listings` orders the enriched listings (surviving ones first).
5. **Build the site** — `build_site` renders the enriched, sorted listings straight into a
   single self-contained `data/site/index.html`, with all listing data embedded directly in the
   page (see "Reopening the app later" below) — nothing is fetched again just to view it.

`jobs/run_real.py` runs all five steps end to end against live data; `jobs/demo_m3.py` runs the
same pipeline against synthetic listings and a small fixture, for a fast, network-free check
that everything is wired correctly.

## Data sources

| Source | Used for | Access |
|---|---|---|
| [Boligsiden](https://www.boligsiden.dk) | The listings themselves (fritidsbolig, i.e. `addressType: "holiday house"`) | Public JSON API (`api.boligsiden.dk`), plain HTTP — no auth, no browser impersonation needed |
| [Geofabrik](https://download.geofabrik.de) OSM extract for Denmark | Coastline, lakes/reservoirs, beaches, marinas, playgrounds, pools, swimming areas, plus named business POIs (hangout/grocery/etc. — catches businesses CVR structurally can't see) | Public `.osm.pbf` download |
| [Danmarks Miljøportal](https://arealdata.miljoeportal.dk) — PULS bathing-water register | Officially designated bathing-water sites (lake eligibility signal) | Public GeoServer WFS, no auth |
| [cvrapi.dk](https://cvrapi.dk) | Business name → registered company address/status | Free, unofficial CVR lookup API |
| Erhvervsstyrelsen `cvr-permanent` | Discovering hangout/grocery/etc. businesses by industry code + postal code — cvrapi.dk can't enumerate, only look up one name at a time | Free system-til-system access (username/password), plain HTTP, Elasticsearch |
| [Datafordeleren](https://datafordeler.dk) — DAR (Danmarks Adresseregister) | Confirming a candidate address is real, and its coordinates | GraphQL, requires a free registered API key |
| [Tavily](https://tavily.com) | Last-resort business discovery for a (town, category) neither CVR nor OSM covered | Free tier (1,000 credits/month), requires a free API key — budget-capped and cached, see `docs/HANDOFF.md` |

## Current status

The core pipeline — fetch, water/amenity scoring, business discovery/resolution, and the
interactive site — is built, and a real end-to-end run (`jobs/run_real.py`) against live
Boligsiden data, the real OSM extract, the real bathing-water register, and a real discovered
business directory works today, verified for one postal code. Hangout and grocery are now
real hard filters, not just water. Business discovery layers three independent sources —
CVR bulk enumeration, OSM-tagged POIs (catches businesses CVR structurally can't see, e.g. a
kro registered under an unrelated holding company), and a budget-capped Tavily web-search
fallback with a persistent cache (only ever searches a given town/category once) — so the
"not registered in CVR at all" gap from earlier is now mostly closed. What's **not** built
yet: automatic nightly re-runs with new-listing notifications, a Fødevarestyrelsen
inspection-report cross-check ("Smiley"), and deployment automation. The address validator is
now rate-limited, so a full-region run with discovery should be safe to attempt, but hasn't
been run at that scale yet — likely slow (thousands of businesses × up to 2 validation calls
each). Today, generating the site is a manual, one-off run.

---

## Running it locally on Windows

### Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** (recommended — a fast, single-binary package/venv
  manager). Without it, a standard `python -m venv` + `pip` works too; substitutions are
  noted below.
- Git (to have cloned this repo)

### 1. Create the virtual environment and install dependencies

In PowerShell, from the repo root:

```powershell
uv venv .venv
uv pip install -e ".[dev]" --python .venv
```

Without `uv`, the equivalent is:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

The optional `llm` extra (`pip install -e ".[llm]"`) is only needed for the last-resort LLM
address-extraction step and requires `ANTHROPIC_API_KEY` — skip it unless you're exercising
that path.

### 2. Set up API keys and configuration

Create a `.env` file in the repo root (already gitignored — never commit it):

```
DATAFORDELER_DAR_API_KEY=<your key>
CVR_USERNAME=<your cvr-permanent username>
CVR_PASSWORD=<your cvr-permanent password>
TAVILY_API_KEY=<your tavily key>
DRIVING_ORIGIN_ADDRESS=Bådehavnsgade 1, 2450 København SV
```

`DATAFORDELER_DAR_API_KEY`: get a free key at [datafordeler.dk](https://datafordeler.dk) —
register an account (email login is enough), create an IT-system under "Datafordelerens
Administration", generate an API key, and request access to **Danmarks Adresseregister
(DAR)**'s GraphQL service specifically. Used to confirm a candidate address is real — including,
now, the driving-time origin address below.

`CVR_USERNAME`/`CVR_PASSWORD`: request free system-til-system access at
[datacvr.virk.dk](https://datacvr.virk.dk/artikel/system-til-system-adgang-til-cvr-data)
(roughly a three-week turnaround) — used to discover hangout/grocery/etc. businesses by
industry code + postal code.

`TAVILY_API_KEY`: sign up free at [tavily.com](https://tavily.com) (no card, ~2 min) — used
only as a last-resort business-discovery fallback for a (town, category) neither CVR nor OSM
covered. Optional: if it's missing, `jobs/run_real.py` logs a warning and skips this fallback
rather than failing the run.

`DRIVING_ORIGIN_ADDRESS`: a plain Danish address (street + house number, postal code, town) —
**required** for `jobs/run_real.py`, which fails loudly at startup if it's missing or doesn't
resolve to a real address, since every listing's drive time depends on it. This is the point of
departure driving times are measured from; change it any time by editing `.env` and re-running —
no cache to clear, since the driving-time cache key includes the origin, so a changed origin
automatically triggers fresh lookups. Geocoded through the same DAR lookup as everything else
(`DATAFORDELER_DAR_API_KEY` above), then queried against
[OSRM](https://project-osrm.org)'s free public routing server — see "Driving time" below.

None of these are needed for the demo site below.

### 3. Run the test suite

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python -m pytest tests/ -q
```

Should report all tests passing, entirely against fixtures — no network access needed.

### 4. Generate the demo site

```powershell
.venv\Scripts\python jobs\demo_m3.py
```

This runs the full scoring + site-building pipeline against a handful of synthetic listings
and a small hand-built OSM fixture (not live data — proves the pipeline is wired correctly).
It writes `data\site\index.html` — open it in a real browser (not just double-click in some
setups; a real browser is needed for the Leaflet map tiles to load) to see the interactive
map and table.

### 5. Run it for real

First, pull the two static datasets the real pipeline needs (both one-time downloads —
re-run only when you want fresher OSM/bathing-water data):

```powershell
.venv\Scripts\python -m screener.fetch.osm_extract --download --pbf data\denmark-latest.osm.pbf --out data\osm_store.pkl
.venv\Scripts\python -c "from screener.fetch.bathing_water import download_badevand_dataset; download_badevand_dataset('data/badevand.geojson')"
```

The OSM extract is a ~500MB download and takes a couple of minutes to parse; the bathing-water
pull is small and fast.

Then run the real pipeline — a live Boligsiden fetch across the postal ranges in
`config/thresholds.yaml`, scored against the real OSM/bathing-water data above:

```powershell
.venv\Scripts\python jobs\run_real.py
```

This writes real listings to `data\site\index.html` for the full configured
Sjælland/Lolland/Falster/Møn scope, with real hangout/grocery/fish_shop/wine_shop/butcher
discovery wired in (`CVR_USERNAME`/`CVR_PASSWORD` required — see step 2). **Note**: the first
run at full regional scope makes a large number of rate-limited (5 req/sec) CVR
address-validation calls plus a ~20-minute OSM business-POI extraction pass — expect it to take
a while, or narrow `postal_ranges` in `config/thresholds.yaml` first to try it quickly.

**Re-running for fresh listings is fast after the first run.** CVR business discovery and OSM
POI extraction only depend on the region, not on which listings exist, so both are cached to
disk (`data\cvr_business_cache.pkl`, `data\osm_poi_cache.pkl`) after the first run and reused
automatically — only the Boligsiden listings fetch and scoring (seconds) actually redo work.
The CVR cache auto-refreshes itself after 7 days (`cvr_cache_max_age_days` in
`config/thresholds.yaml`); the OSM POI cache only rebuilds when `denmark-latest.osm.pbf` itself
is re-downloaded. Pass `--rebuild-discovery-cache` to `jobs\run_real.py` to force both to rebuild
regardless. See `docs/HANDOFF.md` for the current state of each piece and what's next.

### 6. Fixing a bad business (wrong location, wrong category, or closed)

Business discovery occasionally gets one wrong: a company's CVR-registered address is sometimes
an accountant's office or a legacy address, not the real venue, and a CVR branch code doesn't
always match what a business actually sells (a "Restauranter"-classified sole proprietorship
that's really a builder's merchant, say). Rather than trying to fully automate detecting this,
the pipeline reads a small hand-maintained file, `config/business_corrections.yaml`, applied
after CVR, OSM, and Tavily web-search discovery are all merged — so one entry fixes a business
no matter which source found it.

Fixing something you spot on the site only takes one piece of information: the business's exact
name, copied straight from the site (a popup's hangout line, or the table) — no CVR lookup, no
address lookup needed. Two things you can do:

- **`exclude`** a business entirely — for one that's actually closed, or isn't really the kind of
  business it was matched as.
- **`relocate`** a business — for one that resolved to the wrong address (its CVR-registered
  address, say, rather than where it actually operates). Give the corrected street/postal
  code/town; the pipeline re-validates that address against the real Danish address register
  (the same gate every other address in the pipeline goes through), rather than trusting typed-in
  text blindly.

```yaml
exclude:
  - name: "Cafe JaTak ApS"
    reason: "Permanently closed (Google Maps, 2026-09-22)"

relocate:
  - name: "Havblik Agersø"
    street: "Agersø Møllevej 9A"
    postal_code: "4244"
    town: "Agersø By"
    reason: "CVR-registered address is a mailing address, not the restaurant itself"
```

`reason` is free text for future readers — it isn't used by the code. Re-run `jobs\run_real.py`
after editing the file to pick up the change; the CVR/OSM discovery caches don't need rebuilding,
since corrections are applied after they're loaded, not baked into them.

**If an entry stops matching anything** — the business was renamed, or a later run's discovery
sources turn up different results — `jobs\run_real.py` logs a warning naming the stale entry, so
a typo or an outdated fix is never silently ineffective.

### 7. Reopening the app later

`data\site\index.html` is a self-contained static file — all listing data is embedded in it
directly, so just *opening* it (as opposed to regenerating it) never fetches anything over
the network and is instant. You only need to re-run step 5 when you actually want fresher
data.

Serve it locally with Python's built-in server, from the repo root:

```powershell
cd data\site
..\..\.venv\Scripts\python -m http.server 8000
```

Then open **http://localhost:8000/index.html**. Leave that terminal window open — closing it
stops the server. To start it again later (after a reboot, or if you closed the terminal),
just re-run the same two commands.

**Always use the same port (`8000` above, or whatever you pick).** Liked/hidden listings are
stored in your browser, tied to the exact URL you open — a different port looks like a
completely different site to the browser, and your likes/hides won't carry over. Re-running
`jobs\run_real.py` to refresh the data is fine and doesn't affect this — it overwrites the
same file the server is already pointed at, so reload the page and your liked/hidden state
is still there, now against fresh listings.
