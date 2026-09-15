# Summer House Screener

A filter, not a recommender. It pulls holiday-home ("fritidsbolig") listings from Boliga
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

## What it shows

**The map** (Leaflet + OpenStreetMap tiles): one marker per surviving listing. Clicking a pin
pops up its address, open-water breakdown (sea vs. lake, with the eligibility reason), and the
named hangouts/grocery stores/marinas/playgrounds/pools/beaches found nearby.

**The table**, one row per listing, every column sortable by click:

| Column | What it is |
|---|---|
| Open water / Sea / Lake (km) | Distance to the nearest open water, and the sea/lake breakdown |
| Lake signal | Why the nearest lake counts as swimmable (`badevand`, `swimming_area`, `beach_on_shore`, or `none`) |
| Hangout (km) / Hangouts within | Distance to the nearest hangout, and how many fall within the current threshold |
| Grocery (km) | Distance to the nearest grocery store |
| Marina / Playground / Pool / Beach (km) | Informational-only distances — shown, never filtered on |
| Price, m², Lot m², Rooms, Year | Listing basics, straight from Boliga |
| Links | Boliga listing, Google Maps pin/aerial/street view, Apple Maps |

**Live filter controls** above the map/table recompute both the map and the table instantly,
client-side, with no server round-trip and no re-score:
- The three hard-filter distances (water/hangout/grocery), each independently adjustable
- The strict/loose lake-eligibility toggle
- Price range, minimum rooms, minimum m², minimum lot size, minimum build year

## How it's built

**Fetch and score are deliberately separate stages.** Fetching (Boliga listings, the OSM
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

## Data sources

| Source | Used for | Access |
|---|---|---|
| [Boliga](https://www.boliga.dk) | The listings themselves (fritidsbolig, i.e. property type code 4) | Undocumented JSON API, browser-impersonated |
| [Geofabrik](https://download.geofabrik.de) OSM extract for Denmark | Coastline, lakes/reservoirs, beaches, marinas, playgrounds, pools, swimming areas | Public `.osm.pbf` download |
| [Danmarks Miljøportal](https://arealdata.miljoeportal.dk) — PULS bathing-water register | Officially designated bathing-water sites (lake eligibility signal) | Public GeoServer WFS, no auth |
| [cvrapi.dk](https://cvrapi.dk) | Business name → registered company address/status | Free, unofficial CVR lookup API |
| [Datafordeleren](https://datafordeler.dk) — DAR (Danmarks Adresseregister) | Confirming a candidate address is real, and its coordinates | GraphQL, requires a free registered API key |

## Current status

The core pipeline — fetch, water/amenity scoring, business resolution, and the interactive
site — is built and each data source above is confirmed working against live data. What's
**not** built yet: automatic nightly re-runs with new-listing notifications, a Fødevarestyrelsen
inspection-report cross-check ("Smiley"), web-search gap-fill for businesses missing from CVR
and OSM, and deployment automation. Today, generating the site is a manual, one-off run.

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

### 2. Set up API keys

Create a `.env` file in the repo root (already gitignored — never commit it):

```
DATAFORDELER_DAR_API_KEY=<your key>
```

Get a free key at [datafordeler.dk](https://datafordeler.dk): register an account (email
login is enough), create an IT-system under "Datafordelerens Administration", generate an API
key, and request access to **Danmarks Adresseregister (DAR)**'s GraphQL service specifically.
This is only needed for resolving business addresses from raw text — the demo site below
works without it.

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

### 5. Using real data instead of fixtures

Each of these can be pulled for real (all confirmed working live):

**The OSM geometry index** (coastline, lakes, beaches, marinas, playgrounds, pools) — a
~500MB download and a couple of minutes to parse:

```powershell
.venv\Scripts\python -m screener.fetch.osm_extract --download --pbf data\denmark-latest.osm.pbf --out data\osm_store.pkl
```

**The bathing-water register** (for lake eligibility):

```powershell
.venv\Scripts\python -c "from screener.fetch.bathing_water import download_badevand_dataset; download_badevand_dataset('data/badevand.geojson')"
```

**Boliga listings** and **business resolution** (CVR + DAR address validation) are driven
through `screener.fetch.boliga.BoligaClient` and `screener.resolve.pipeline.resolve_business_address`
respectively — there isn't a single one-shot "fetch everything real" script yet (that's
planned nightly-orchestration work), but both are directly importable and working against
live data today. See `docs/HANDOFF.md` for the current state of each piece and what's next.
