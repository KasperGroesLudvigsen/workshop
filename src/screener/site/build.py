"""Static site generation: embeds scored listings as JSON and renders
index.html (Leaflet map + sortable table). No backend — filtering,
sorting, and count/name recomputation for adjustable thresholds all happen
client-side in the embedded script, against the JSON this module writes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from screener.config import Settings

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _config_json(settings: Settings) -> dict[str, Any]:
    return {
        "water_km": settings.water_km,
        "hangout_km": settings.hangout_km,
        "grocery_km": settings.grocery_km,
        "lake_strict": settings.lake_strict,
        "min_lake_area_ha": settings.min_lake_area_ha,
        "amenity_search_radius_km": settings.amenity_search_radius_km,
    }


def build_site(scored_listings: list[dict[str, Any]], settings: Settings, out_path: Path | str) -> Path:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(disabled_extensions=("j2",)),
    )
    template = env.get_template("index.html.j2")
    config = _config_json(settings)
    html = template.render(
        listings=scored_listings,
        listings_json=json.dumps(scored_listings),
        config=config,
        config_json=json.dumps(config),
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
