# Getting the Boliga API details

**What this is for**: Boliga's search API is undocumented. Six parameter names, the
Fritidshus property-type code, and a set of response field names were written against
secondhand write-ups and have never been checked against the real service. Until they are,
no listings flow through the pipeline — everything downstream is built and waiting.

**Why you and not me**: `api.boliga.dk` sits behind a Cloudflare interactive challenge. It
answers every request from a datacenter with `403` and a "Just a moment..." page. I tried
three ways round it and all three fail for environment reasons, not Boliga ones:

| Attempt | Result |
|---|---|
| `curl_cffi` browser impersonation (what the client uses) | TLS reset — the impersonated fingerprint can't negotiate through the sandbox's proxy |
| Headless Chromium (would pass the challenge) | Can't reach the network at all — even `example.com` resets |
| Plain `requests` / `curl` | Gets the challenge page, as expected |

From an ordinary home or office connection none of that applies. **Expect this to take about
five minutes.**

---

## Route A — run the script (try this first)

From the repo, on a normal internet connection:

```bash
cd /path/to/workshop
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"

PYTHONPATH=src python3 jobs/discover_boliga.py
```

It makes three requests (one per second, one per sample postal code), prints a report, and
writes `data/boliga_discovery.json`.

**What you'll see** — a property-type table with the answer marked:

```
====================================================================
PROPERTY TYPE CODES  (look for Fritidshus / Fritidsbolig / Sommerhus)
====================================================================
  propertyType=1     Villa
  propertyType=2     Rækkehus
  propertyType=3     Ejerlejlighed
  propertyType=4     Fritidshus            <== THIS ONE
  ...
```

(Codes above are illustrative — take whatever your run actually prints.)

Then a response-shape section listing the real top-level keys, the real per-listing field
names, and which of the thirteen fields the scorer wants have an obvious match.

### What to send back

Just **`data/boliga_discovery.json`**. It contains the observed key names and one complete
sample listing, which answers every open question in one go — no round-tripping individual
questions. It's public for-sale listing data; there are no credentials or personal data in
it.

Either commit it, or paste the terminal output if you'd rather not.

### If you want to set the one value yourself

The single most important result is the Fritidshus code. In `config/thresholds.yaml`:

```yaml
boliga_property_type_fritidsbolig: 4    # replace null with the code your run printed
```

That alone unblocks the pipeline. The response field names can follow.

---

## Route B — browser DevTools (only if Route A fails)

Use this if the script reports `BLOCKED` or the property-type table comes back empty.

1. Open <https://www.boliga.dk/> in Chrome or Firefox.
2. Press **F12** → **Network** tab → click **Fetch/XHR** to filter.
3. On the site, search for holiday homes: set the area to a postal code like **4200**, and
   under property type tick **Fritidshus**. Hit search.
4. In the Network panel, click the request to **`api.boliga.dk/api/v2/search/results`**.

Now read two things off it:

**The request** — the *Headers* tab, "Query String Parameters". Note the exact parameter
names and the value next to the property-type one. Compare against what the code sends:

| Code sends | Real name? |
|---|---|
| `zipcodeFrom` | |
| `zipcodeTo` | |
| `page` | |
| `pageSize` | |
| `sort` | |
| `propertyType` | ← and what integer did Fritidshus produce? |

**The response** — the *Response* (or *Preview*) tab. Right-click → **Copy response**, and
send me that, or note:

- The top-level key holding the total count. Code tries `meta.totalCount`, `totalResults`,
  `total`.
- The top-level key holding the array of listings. Code tries `results`, `listings`, `items`.
- The field names inside one listing (id, coordinates, price, size, rooms, build year, …).

---

## What I'll do with it

All the uncertain names are isolated in `src/screener/fetch/boliga.py` specifically so
correcting them is a small, contained edit:

- `_PARAM_NAMES` — the six query parameter names
- `_extract_total_count()` / `_extract_listings()` — the response-key candidates
- `normalize_listing()` — the per-listing field candidates
- `config/thresholds.yaml` → `boliga_property_type_fritidsbolig`

Once those are confirmed I'll fix any that differ, run a real postal shard end to end, and
check the generated map against a handful of listings by hand.

---

## Notes

- **Be gentle.** The client already enforces 1 request/second and the discovery script makes
  three requests total. Don't raise the rate — a scraping-shaped access pattern is what gets
  an IP challenged in the first place.
- **Set a real User-Agent before any production run.** `config/thresholds.yaml` →
  `boliga.user_agent` is currently a generic Chrome string.
- **Cloudflare's challenge vindicates the design.** The client was built around `curl_cffi`
  browser impersonation on the theory that Boliga might block plain clients. It does. Keep
  that transport; it's likely to be necessary on your machine too, and the client already
  treats a 403 as a hard failure rather than degrading it into an empty result set.
