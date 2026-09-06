"""One-shot discovery run against the live Boliga API.

Run this from a machine on ordinary consumer internet (see
``docs/BOLIGA_DISCOVERY.md``). It answers, in a single pass, every question
about Boliga's API that could not be verified from the build environment:

  1. Do the query parameter names in ``_PARAM_NAMES`` actually work?
  2. Which ``propertyType`` code is Fritidshus/fritidsbolig?
  3. What are the real top-level response keys (total count, listings array)?
  4. What are the real per-listing field names?

It makes at most three requests, one per second, and writes two files:

  - ``data/boliga_discovery.json`` — a trimmed raw sample plus the observed
    key names. This is the file to hand back: it answers every open question
    at once, so nobody has to round-trip individual questions.
  - a printed report, so you can read the answer without opening anything.

Nothing here is part of the nightly pipeline; it exists to be run once.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from screener.config import REPO_ROOT, load_settings
from screener.fetch.boliga import (
    _PARAM_NAMES,
    BoligaBlockedError,
    BoligaClient,
    _extract_listings,
    _extract_total_count,
    discover_property_types,
)

logger = logging.getLogger(__name__)

# Mixed-stock postal codes: somewhere with both year-round housing and
# holiday homes, so an unfiltered page is likely to contain a Fritidshus.
DEFAULT_SAMPLE_ZIPS = (4200, 4560, 3210)

#: Fields the scorer reads. Printed alongside what the API actually returns
#: so a missing one is obvious at a glance.
WANTED_FIELDS = (
    "id", "lat", "lon", "price", "size_m2", "lot_size_m2", "rooms",
    "build_year", "energy_class", "days_on_market", "address", "zip_code", "url",
)


def _probe_zip(client: BoligaClient, zip_code: int) -> dict:
    """One unfiltered page for a postal code, plus everything observable."""
    body = client.search_page(
        zip_from=zip_code, zip_to=zip_code, property_type=None, page=1, page_size=100
    )
    result: dict = {"zip": zip_code, "top_level_keys": sorted(body.keys())}

    if isinstance(body.get("meta"), dict):
        result["meta_keys"] = sorted(body["meta"].keys())

    try:
        result["total_count"] = _extract_total_count(body)
    except KeyError as exc:
        result["total_count_error"] = str(exc)

    try:
        listings = _extract_listings(body)
    except KeyError as exc:
        result["listings_error"] = str(exc)
        return result

    result["listing_count"] = len(listings)
    if listings:
        result["listing_keys"] = sorted(listings[0].keys())
        # One whole listing, verbatim. Public for-sale data, no credentials.
        result["sample_listing"] = listings[0]
        types: dict[str, set[str]] = {}
        for listing in listings:
            code = listing.get("propertyType")
            if code is None:
                continue
            label = (
                listing.get("propertyTypeName")
                or listing.get("propertyType_da")
                or listing.get("type")
            )
            types.setdefault(str(code), set())
            if label:
                types[str(code)].add(str(label))
        result["property_types"] = {code: sorted(labels) for code, labels in types.items()}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--zips", type=int, nargs="+", default=list(DEFAULT_SAMPLE_ZIPS),
        help="postal codes to sample (one request each, 1 req/sec)",
    )
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "boliga_discovery.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = load_settings()
    client = BoligaClient(settings.boliga)

    print(f"Requesting {settings.boliga.base_url}")
    print(f"Sending parameter names: {sorted(_PARAM_NAMES.values())}\n")

    probes = []
    for zip_code in args.zips:
        try:
            probes.append(_probe_zip(client, zip_code))
        except BoligaBlockedError as exc:
            print(f"\nBLOCKED on {zip_code}: {exc}")
            print("Cloudflare is challenging this machine too. See docs/BOLIGA_DISCOVERY.md")
            print("for the browser-DevTools fallback.")
            return 2
        except KeyError as exc:
            # The request itself worked; only our key guesses were wrong.
            # That is a successful discovery run, not a failure — the whole
            # point is to learn the real names.
            print(f"note: {zip_code}: {exc}")
            probes.append({"zip": zip_code, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — transport-level, report don't trace
            print(f"\nCOULD NOT REACH BOLIGA on {zip_code}: {type(exc).__name__}: {exc}")
            print(
                "\nIf this says 'Connection reset' or a TLS error, the machine is behind a\n"
                "proxy that breaks curl_cffi's browser impersonation — that is what blocks\n"
                "the build environment. Run this from an ordinary home/office connection,\n"
                "or use the browser-DevTools fallback in docs/BOLIGA_DISCOVERY.md."
            )
            return 2

    merged: dict[str, set[str]] = {}
    for probe in probes:
        for code, labels in (probe.get("property_types") or {}).items():
            merged.setdefault(code, set()).update(labels)

    print("=" * 68)
    print("PROPERTY TYPE CODES  (look for Fritidshus / Fritidsbolig / Sommerhus)")
    print("=" * 68)
    for code, labels in sorted(merged.items(), key=lambda kv: str(kv[0])):
        marker = "  <== THIS ONE" if any(
            w in " ".join(labels).lower() for w in ("fritid", "sommerhus")
        ) else ""
        print(f"  propertyType={code:<5} {', '.join(sorted(labels)) or '(no label in payload)'}{marker}")
    if not merged:
        print("  none found — the sample pages carried no propertyType field.")
        print("  Use the DevTools fallback in docs/BOLIGA_DISCOVERY.md.")

    first = next((p for p in probes if p.get("listing_keys")), None)
    if first:
        print("\n" + "=" * 68)
        print("RESPONSE SHAPE")
        print("=" * 68)
        print(f"  top-level keys : {first['top_level_keys']}")
        if "meta_keys" in first:
            print(f"  meta keys      : {first['meta_keys']}")
        print(f"  total count    : {first.get('total_count', first.get('total_count_error'))}")
        print(f"\n  listing fields : {first['listing_keys']}")
        print("\n  fields the scorer wants, and whether an obvious match exists:")
        available = {k.lower() for k in first["listing_keys"]}
        for wanted in WANTED_FIELDS:
            hit = [k for k in first["listing_keys"] if wanted.replace("_", "") in k.lower().replace("_", "")]
            print(f"    {wanted:16} {'✓ ' + ', '.join(hit) if hit else '? no obvious match'}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"probes": probes}, indent=2, ensure_ascii=False, default=str))
    print(f"\nWrote {args.out}")
    print("Hand that file back — it answers every open question in one go.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
