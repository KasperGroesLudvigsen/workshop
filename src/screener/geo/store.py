"""Spatial index over OSM geometry layers (coastline, beach, lake, marina,
playground, pool, swimming_area).

Built once by ``fetch/osm_extract.py`` and queried repeatedly by
``geo/water.py`` and ``geo/amenities.py``. All geometries are stored in a
projected CRS (meters, see ``geo/projection.py``) so nearest/within-radius
queries are plain Euclidean distance against an ``STRtree`` index, not
error-prone lon/lat arithmetic.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import shapely
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree


@dataclass
class Layer:
    """One category's geometries (already projected) with parallel metadata
    records, backed by an STRtree for nearest/within-radius queries."""

    geoms: list[BaseGeometry]
    records: list[dict[str, Any]]
    tree: STRtree | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        assert len(self.geoms) == len(self.records)
        self.tree = STRtree(self.geoms) if self.geoms else None

    def __len__(self) -> int:
        return len(self.geoms)

    def nearest(self, point: Point) -> tuple[dict[str, Any], float, BaseGeometry] | None:
        """Nearest (record, distance_m, geometry), or None if the layer is
        empty."""
        if self.tree is None:
            return None
        idx = int(self.tree.nearest(point))
        return self.records[idx], float(self.geoms[idx].distance(point)), self.geoms[idx]

    def within(self, point: Point, radius_m: float) -> list[tuple[dict[str, Any], float, BaseGeometry]]:
        """All (record, distance_m, geometry) within radius_m, sorted by
        ascending distance."""
        if self.tree is None:
            return []
        candidate_idx = self.tree.query(point.buffer(radius_m))
        out: list[tuple[dict[str, Any], float, BaseGeometry]] = []
        for idx in candidate_idx:
            dist = float(self.geoms[idx].distance(point))
            if dist <= radius_m:
                out.append((self.records[idx], dist, self.geoms[idx]))
        out.sort(key=lambda triple: triple[1])
        return out

    def intersecting(self, geom: BaseGeometry, buffer_m: float = 0.0) -> list[dict[str, Any]]:
        """Records whose geometry intersects `geom` (optionally buffered).
        Used for lake-eligibility checks: does a swimming_area or beach
        touch this specific lake polygon?"""
        if self.tree is None:
            return []
        query_geom = geom.buffer(buffer_m) if buffer_m else geom
        candidate_idx = self.tree.query(query_geom)
        return [self.records[idx] for idx in candidate_idx if self.geoms[idx].intersects(query_geom)]


LAYER_NAMES = ("coastline", "beach", "lake", "marina", "playground", "pool", "swimming_area")


@dataclass
class GeometryStore:
    coastline: Layer
    beach: Layer
    lake: Layer
    marina: Layer
    playground: Layer
    pool: Layer
    swimming_area: Layer

    def save(self, path: Path | str) -> None:
        payload: dict[str, Any] = {}
        for name in LAYER_NAMES:
            layer: Layer = getattr(self, name)
            payload[name] = {
                "wkb": [shapely.to_wkb(g) for g in layer.geoms],
                "records": layer.records,
            }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(payload, f)

    @classmethod
    def load(cls, path: Path | str) -> "GeometryStore":
        with open(path, "rb") as f:
            payload = pickle.load(f)
        kwargs = {}
        for name in LAYER_NAMES:
            data = payload[name]
            geoms = [shapely.from_wkb(w) for w in data["wkb"]]
            kwargs[name] = Layer(geoms=geoms, records=data["records"])
        return cls(**kwargs)
