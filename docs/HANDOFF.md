# Handoff — Summer House Screener

Read this first if you're picking this project back up. This is the "what's actually true
right now" status report; `docs/PLAN.md` has the milestone detail.

**What changed in this session**: the previous build session had almost no internet egress,
so five external data sources were written against documented-or-guessed shapes and never
hit a real server. Four of those five are now verified against live services, and the code
changed where reality disagreed with the guess. The fifth (Boliga) is blocked for an
environment reason, not a design reason — details below.

## tl;dr

- Branch: `claude/resume-work-handoff-mx75xd` on `KasperGroesLudvigsen/workshop`.
- M0–M5 done. 73 tests pass (was 49).
- **Address validation is no longer stubbed** — this was the one item blocking M5 from doing
  anything real, and it now runs against the live Danish address register.
- **Bathing water** now reads the real PULS register instead of a guessed CSV schema.
- **cvrapi.dk** had a real bug (a quota block read as "business doesn't exist"); fixed.
- **Boliga remains unverified** — Cloudflare blocks this environment. It is the only item
  still needing a machine with ordinary consumer internet.
- M6–M10 not started.

## How to get running

```bash
cd /path/to/workshop
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
PYTHONPATH=src python3 -m pytest tests/ -q          # should show 73 passed
PYTHONPATH=src python3 jobs/demo_m3.py             # writes data/site/index.html
```

The whole test suite runs offline — every external client is driven through an injectable
transport seam against captured real responses.

## Verified this session

### 1. Address validation — DAWA's replacement — RESOLVED ✅

This was flagged as "the one genuinely blocking item for M5". It is now implemented and
measured.

- **DAWA's status**: it has not shut down yet, but it is going. `api.dataforsyningen.dk`
  still answers and sends `sunset` / `deprecation` headers, and Klimadatastyrelsen has
  announced **permanent shutdown on 2026-10-01 10:00** — a few weeks from now. Do not build
  anything new on it. (The previous handoff's "shut down 2026-07-01" was close but early.)
- **The replacement** is Klimadatastyrelsen's **Adressevaelger**, at `https://adressevaelger.dk`:
  `GET /adresser/soeg?tekst=…&token=…` to search, `GET /adresser/{id}?token=…` (or
  `/husnumre/{id}`) for the full record. Its sibling "Adressevask" is the announced
  replacement for DAWA's *datavask*, but it is not exposed on this host and was not needed:
  search + record lookup does the job.
- **New module**: `src/screener/resolve/adressevaelger.py`. It implements the existing
  `AddressValidator` protocol, so this was the one-class swap the seam was designed for —
  `resolve/pipeline.py` is untouched.
- The register returns coordinates in **EPSG:25832**, the same CRS `geo/projection.py`
  already uses, so no new dependency.

**Measured, not assumed**: 50 real addresses pulled from the national address register
across ten target postal codes → **50/50 validated, coordinates agreeing to 0.00 m**. Five
negative cases (wrong town, nonexistent street, nonexistent house number) all correctly
rejected.

Two things worth knowing before you touch this module:

- **The search is fuzzy and will happily answer with the wrong town.** "Rentemestervej 8"
  matches both 2400 København NV and 3400 Hillerød. The validator re-checks postal code *and*
  house number against the fetched record. Do not remove that check to "improve the hit
  rate" — it is the entire register gate.
- **Two query forms are tried, deliberately.** The API's own `postnummer` parameter is
  accepted and then *ignored* (2400 and 3400 return byte-identical hits), so the postal code
  can only be steered through the free text — and including it sometimes returns zero hits,
  while omitting it lets a common street name ("Søvej 1", one in nearly every town) crowd the
  wanted town past the result cap. Each form alone misses addresses; both together got 50/50.

**Credential note**: the token in `config/thresholds.yaml` is `adressevaelger123`, the demo
token from Klimadatastyrelsen's own published README. It works today and is fine for
development. Get a real one before production: <https://confluence.sdfi.dk/display/ADV/Brugerstyring>.

### 2. Bathing water dataset — RESOLVED ✅

The placeholder `badevand.dk` URL and invented CSV columns are gone. The real source is
**Danmarks Miljøportal's PULS register**, layer `puls:Badevand`, public GeoServer WFS at
`https://pulsgeo.miljoeportal.dk/geoserver/wfs`, published CC0. No account needed.

Verified live: 1,488 features, **1,039 open sites** once stations carrying a `Closed` date
are dropped, of which **123 are `Ferskvand`** (freshwater — the lake sites this signal exists
for). That 1,039 independently matches the published count of official Danish bathing sites,
which is the cross-check that the closed-site filter is right.

Two plausible alternatives were checked and rejected; they're documented in the module so
nobody burns another hour on them:

- **EMODnet** `emodnet:bathingwaters` — easy to query and well documented, but it is a
  *marine* portal. All 27,794 Danish rows are "Coastal Bathing Water" and the layer carries
  no lake sites for any country, making it exactly useless for a lake-eligibility signal.
- **Miljøportal's "Badevand: Analyse- og Måleresultater" CSV** — the per-sample E. coli
  measurement series. No coordinates, and the download is behind an authenticated portal
  account (401).

### 3. cvrapi.dk — VERIFIED, and a real bug fixed ✅

Field names `vat`, `name`, `address`, `zipcode`, `city`, `enddate` are confirmed against
cvrapi.dk's own documentation and published response examples. (`vat` is an int and `zipcode`
may be either — the existing `str()` coercions were right.) The binavne/trading-names key is
still unconfirmed; all three candidate spellings are still accepted.

**The bug**: cvrapi.dk returns *every* error as **HTTP 200 with an `error` key**. The client
mapped any `error` key to `CvrNotFoundError`, so `QUOTA_EXCEEDED`, `BANNED` and `INVALID_UA`
all came back as "no CVR match" — a block silently reading as "this business doesn't exist",
which is the exact failure mode the Boliga client goes out of its way to refuse. Blocking
codes now raise `CvrBlockedOrRateLimitedError`; only `NOT_FOUND` / `INVALID_VAT` are misses.

**Two constraints that affect the design, not just the code:**

- **The free allowance is 50 lookups per day, per IP _range_.** This is low enough to be
  architectural — resolving a few hundred candidate businesses across Sjælland is *days* of
  budget. See "Recommended: switch to the official CVR API" below; this changes the original
  "cvrapi.dk for low setup cost" decision, which was made without knowing this number.
- **Generic user agents are rejected** with `INVALID_UA`. The documented form is
  `[company] - [project] - [contact name] [phone or email]`. `DEFAULT_USER_AGENT` now carries
  the template; it must be filled in before any real run.

It could not be called live from here — the shared egress IP range is already over quota
(`QUOTA_EXCEEDED` on every attempt, from several different IPs). That's an environment
limitation, not a code problem.

### 3b. Recommended: switch to the official CVR API ⚠️ needs one email from you

Investigated this session because the 50/day quota makes cvrapi.dk unusable at the scale
this project needs. **The official Erhvervsstyrelsen distribution solves both of the
problems the plan flagged, not just the quota one.**

- **Endpoint**: `http://distribution.virk.dk/cvr-permanent` (Elasticsearch 1.7.4, HTTP only
  — no HTTPS). Confirmed reachable: it answers `401 Authorization Required`, so the host and
  index name are right and only credentials are missing.
- **Auth**: HTTP Basic. **Free.** Request credentials by emailing `cvrselvbetjening@erst.dk`;
  they issue a username/password and ask you to sign a declaration about handling
  advertisement-protected (*reklamebeskyttede*) entities.
- **It enumerates.** This is the important part. The plan notes cvrapi.dk "cannot answer
  'list every hangout in postal code 4200'" and that discovery therefore needs OSM POIs or
  M7's web search. The official API answers exactly that query:

  | Need | Field path |
  |---|---|
  | industry code | `VrproduktionsEnhed.hovedbranche.branchekode` |
  | postal code | `VrproduktionsEnhed.beliggenhedsadresse.postnummer` |

  with the scroll API for result sets over 3,000.

- **Use the `produktionsenhed` index, not `virksomhed`.** A production unit (P-number) is a
  *physical site* with its own address; a company (CVR number) is a legal entity. A
  supermarket chain is one company and hundreds of shops — and this project cares about
  shops. 2,787,126 production units vs. 2,194,982 companies.

**Why this matters beyond cost**: if CVR can enumerate by industry code and postal code,
it becomes the discovery mechanism the original brief assumed it was, which likely shrinks
or removes M7's dependency on a Brave Search API key you do not yet have.

**Not yet implemented** — deliberately. The whole lesson of this session is that writing a
client against an unverified shape costs more than it saves, and this one cannot be verified
without credentials. `resolve/cvr_match.py`'s interface is the seam; swapping the client
behind it is the same shape of change as the address validator turned out to be. Send the
email, and the client can be built and measured against the real thing.

Relevant DB07 industry codes to confirm once you have access: 471110 (købmænd/døgnkiosker),
471120 (supermarkeder), 471130 (discountforretninger), 472200 (slagter), 472300
(fiskeforretninger), 472500 (vinforretninger), 561010 (restauranter), 563000 (caféer,
værtshuse).

### 4. Real OSM extract — RESOLVED ✅ (via a mirror)

`download.geofabrik.de` is **unreachable from this environment** (connection reset at the
egress proxy), as is `overpass-api.de` and every public Overpass instance tried.
`https://download.openstreetmap.fr/extracts/europe/denmark.osm.pbf` serves the same country
extract and works. `fetch/osm_extract.py` now takes a mirror list and falls through on
failure, with Geofabrik still first. `download_geofabrik_extract` is kept as an alias so
nothing that already calls it breaks.

See "Real-extract results" below for the layer counts.

## Still blocked — needs a machine with ordinary internet

### Boliga API parameters (`src/screener/fetch/boliga.py`) ⛔

**Still unverified, and not fixable from a datacenter.** `api.boliga.dk` now sits behind a
Cloudflare interactive challenge — it answers `403` with `cf-mitigated: challenge` and a
"Just a moment..." body to every plain client, regardless of User-Agent. Three routes were
tried:

- `curl_cffi` browser impersonation (what the module is built around): dies at TLS through
  this environment's MITM proxy — the impersonated fingerprint and the proxy's TLS terminator
  can't negotiate.
- Headless Chromium (pre-installed, would pass the challenge): **cannot reach the network
  through this proxy at all** — `example.com` also fails with `ERR_CONNECTION_RESET`. So
  this is an environment limitation, not a Boliga one.
- Plain `requests`/`curl`: gets the challenge page, as expected.

So this needs a normal machine on consumer internet. **See `docs/BOLIGA_DISCOVERY.md`** —
step-by-step instructions written for exactly that, with a runnable script
(`jobs/discover_boliga.py`) that answers all four open questions in three requests and
writes `data/boliga_discovery.json` to hand back, plus a browser-DevTools fallback if
Cloudflare challenges that machine too.

Note that `curl_cffi` impersonation is likely to be *necessary* there too — the Cloudflare
challenge is real and the module's choice of transport is vindicated, it just can't be
exercised from here.

## Credentials / access still needed from the user

- **Brave Search API key** — required before M7 can do anything for real.
- **A real contact string** for the cvrapi.dk User-Agent (`DEFAULT_USER_AGENT` in
  `fetch/cvr.py`) and the Boliga UA. cvrapi.dk *rejects* generic agents outright.
- **CVR credentials** — email `cvrselvbetjening@erst.dk` for free Basic-auth access to the
  official distribution. This is the single highest-value credential to request: it removes
  the 50/day quota *and* unlocks enumeration by industry + postal code. See §3b.
- **A production Adressevaelger token** — the demo token works but isn't meant for real use.
- **Hetzner account + server** — needed before M10's deployment steps can run.
- **ntfy topic name** — trivial, just pick a string.
- **`ANTHROPIC_API_KEY`** if you want `resolve/llm_extract.py`'s last-resort step to run
  (`pip install -e ".[llm]"`). Least urgent — steps 1–3 should resolve most businesses.

## Decisions already made (don't re-litigate without new information)

- **Stack**: Python, shapely 2.0 (STRtree) + pyproj + DuckDB + pyosmium + curl_cffi + Jinja2.
- **Address register**: Adressevaelger (see above). DAWA is dead on 2026-10-01.
- **Bathing water**: Miljøportal PULS `puls:Badevand` WFS (see above).
- **CVR access**: cvrapi.dk — **but see the 50/day quota finding**, which is new information
  and is exactly the kind of thing that justifies revisiting it.
- **Notifications**: ntfy (topic name, no account) over SMTP.
- **Lake eligibility default**: strict (`lake_strict: true`), with both strict and loose
  always computed so the UI toggle needs no re-score.
- **Min lake area**: 5 ha, configurable.
- **Distance function**: straight-line haversine now (`geo/distance.py`), isolated behind
  `get_distance_fn()` so a drive-time backend can replace it later.

## What's not started at all

- **M6 (findsmiley)**: `fetch/findsmiley.py` doesn't exist. Should fetch a business's
  Fødevarestyrelsen inspection report (found via the link `resolve/jsonld.py`'s
  `find_findsmiley_link` already extracts) and attach inspection date + address cross-check.
  `findsmiley.dk` is reachable from here, so this one is not blocked.
- **M7 (web-search gap-fill)**: `fetch/web_search.py` doesn't exist. Needs a **Brave Search
  API key**. Search per town, not per listing; feed discovered names through the *existing*
  `resolve/pipeline.py` unchanged — never skip the address-register gate just because a name
  came from search.
- **M8 (nightly orchestration)**: `jobs/nightly.py` doesn't exist. fetch (M1) → score
  (M3–M7) → diff against yesterday (`db.py`'s `save_scored_listings` / `passing_listing_ids`
  are already implemented) → notify via ntfy on new passes.
- **M9 (site polish), remainder**: the template already has live typed-input filtering for
  price/rooms/m²/lot/build-year/the three thresholds/lake-strict, all client-side. Still
  missing: per-category **minimum-count** filters ("at least 3 hangouts within threshold"),
  and table columns for every field per the brief — address, energy class, days on market,
  and the four CVR categories in the schema but not yet columns (fish_shop, wine_shop,
  ice_cream, butcher).
- **M10 (deployment)**: nothing written. systemd `.service`/`.timer` units for both jobs and
  a Hetzner runbook (CX22 sizing, env vars, one-time manual steps). Server provisioning is
  the user's action.

## Test suite map (73 tests, all passing, all offline)

| File | Covers |
|---|---|
| `test_boliga.py` | Sharding, 300-cap bisection, truncation assertion, 403 handling, `discover_property_types` |
| `test_osm_extract.py` | pyosmium extraction, area computation, spatial queries against the synthetic fixture |
| `test_water.py` | Lake area filtering, eligibility signals, strict vs. loose divergence |
| `test_bathing_water.py` | PULS GeoJSON parsing, closed-station and "Ukendt" rejection, schema-drift raising, spatial site-to-lake matching |
| `test_cvr.py`, `test_cvr_match.py` | CVR parsing, fuzzy name matching, closed-business rejection, **quota/ban vs. genuine-miss separation** |
| `test_adressevaelger.py` | **Register gate**: wrong-town and wrong-house-number rejection, both query forms, coordinate conversion, service errors raising rather than reading as no-match |
| `test_jsonld.py` | LocalBusiness/PostalAddress extraction, `@graph`-wrapped JSON-LD, findsmiley link extraction |
| `test_address_regex.py` | Danish address regex, `NotConfiguredValidator` raising rather than accepting |
| `test_resolve_pipeline.py` | All 4 resolution steps in order, and a hallucinated LLM address being offered to the validator and dropped |
| `test_score_pipeline.py` | End-to-end scoring: water hard filter, OSM categories, business-directory wiring, sort order |

Fixtures: `sample.osm.xml` (hand-built, real Sjælland coordinates near Sorø),
`badevand_sample.geojson` and `adressevaelger_sample.json` (real response *shapes* captured
from the live services).

## Suggested order for the next session

1. **Boliga params**, from a machine with ordinary internet — it's the last verification gap
   and it gates every real listing.
2. Run the full pipeline on one real postal shard end to end and eyeball it against a map.
3. M6 (findsmiley — not blocked) → M7 (get the Brave key first) → M8 → M9 remainder → M10.
