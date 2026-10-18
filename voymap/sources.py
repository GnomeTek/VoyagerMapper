"""Basemap / overlay tile source registry.

Two source kinds:
  * ``xyz``           — a URL template with {z}, {x}, {y} (and optional {s} for a
                        subdomain rotation). ArcGIS tile caches use z/y/x order;
                        that's just a different template.
  * ``arcgis_export`` — a dynamic ArcGIS MapServer ``/export`` endpoint. One request
                        per tile, with the tile's EPSG:3857 bbox. Slower than a cache,
                        but it's the only way to get services that aren't cached
                        (e.g. USFS MVUM).

Verification status is tracked honestly per source (``verified``): a source marked
False has a URL I could not confirm and may need correcting. Users can override or
add sources in ``~/.config/voymap/sources.json`` (same JSON shape as ``to_dict``).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .tilemath import tile_bounds_3857

USER_AGENT = "voymap/0.1 (+https://github.com; personal offline-map builder for Trail Tech Voyager Pro)"


@dataclass
class TileSource:
    id: str
    name: str
    kind: str                      # "xyz" | "arcgis_export"
    url: str
    attribution: str = ""
    min_zoom: int = 0
    max_zoom: int = 16
    opaque: bool = True            # True = base layer; False = transparent overlay
    bulk_ok: bool = True           # False = preview only; never bulk-download
    rate_limit: float = 8.0        # max requests/second for this source
    max_workers: int = 4
    subdomains: str = ""           # e.g. "abc" for {s}
    headers: dict = field(default_factory=dict)
    verified: bool = True
    notes: str = ""
    # arcgis_export only:
    export_params: dict = field(default_factory=dict)
    user: bool = False             # defined in the user's sources.json (editable/removable in the app)

    def validate(self) -> list[str]:
        """Human-readable problems; empty list means usable."""
        errs = []
        if not re.fullmatch(r"[a-z0-9_\-]{1,40}", self.id or ""):
            errs.append("id must be 1-40 chars of a-z, 0-9, _ or -")
        if not self.name.strip():
            errs.append("name is required")
        if self.kind not in ("xyz", "arcgis_export"):
            errs.append("kind must be xyz or arcgis_export")
        u = (self.url or "").strip()
        if not u.lower().startswith(("http://", "https://")):
            errs.append("url must start with http:// or https://")
        if self.kind == "xyz":
            for tok in ("{z}", "{x}", "{y}"):
                if tok not in u:
                    errs.append(f"xyz url must contain {tok}")
            if "{s}" in u and not self.subdomains:
                errs.append("url uses {s} but no subdomains are given")
        if not (0 <= self.min_zoom <= self.max_zoom <= 22):
            errs.append("zoom range must satisfy 0 <= min <= max <= 22")
        if not (0.1 <= self.rate_limit <= 50):
            errs.append("rate limit must be 0.1-50 requests/s")
        if not (1 <= self.max_workers <= 16):
            errs.append("workers must be 1-16")
        return errs

    def tile_url(self, x: int, y: int, z: int) -> str:
        if self.kind == "xyz":
            s = ""
            if self.subdomains:
                s = self.subdomains[(x + y) % len(self.subdomains)]
            return self.url.format(z=z, x=x, y=y, s=s)
        if self.kind == "arcgis_export":
            xmin, ymin, xmax, ymax = tile_bounds_3857(x, y, z)
            params = {
                "bbox": f"{xmin:.6f},{ymin:.6f},{xmax:.6f},{ymax:.6f}",
                "bboxSR": "3857",
                "imageSR": "3857",
                "size": "256,256",
                "format": "png32",
                "transparent": "true" if not self.opaque else "false",
                "dpi": "96",
                "f": "image",
            }
            params.update(self.export_params)
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            base = self.url.rstrip("/")
            if not base.endswith("/export"):
                base += "/export"
            return f"{base}?{qs}"
        raise ValueError(f"unknown source kind {self.kind!r}")

    def leaflet_spec(self) -> dict:
        """What the GUI's Leaflet page needs to preview this source."""
        d = {
            "id": self.id, "name": self.name, "kind": self.kind, "url": self.url,
            "attribution": self.attribution, "minZoom": self.min_zoom,
            "maxZoom": self.max_zoom, "opaque": self.opaque, "subdomains": self.subdomains,
            "verified": self.verified, "bulkOk": self.bulk_ok,
        }
        if self.kind == "arcgis_export":
            d["exportParams"] = self.export_params
        return d

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Built-in sources
# --------------------------------------------------------------------------
# URLs below were checked against the service directories on 2026-10-17 unless
# marked verified=False. Max zooms are the cache levels the services advertise;
# a 404/blank past the real cache limit is handled by the fetcher.

BUILTIN: list[TileSource] = [
    TileSource(
        id="usgs_topo",
        name="USGS Topo (The National Map)",
        kind="xyz",
        url="https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
        attribution="USGS The National Map",
        min_zoom=0, max_zoom=16, opaque=True, bulk_ok=True,
        rate_limit=10.0, max_workers=6,
        notes="Cached tile service, public domain. The service definition advertises LODs 0-23 "
              "and USGS announced cache content to level 17; 16 is the safe default (contours "
              "are drawn to ~1:18k). Raise max_zoom to 17 if those tiles come back non-blank for you.",
    ),
    TileSource(
        id="usgs_imagery_topo",
        name="USGS Imagery + Topo",
        kind="xyz",
        url="https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}",
        attribution="USGS The National Map",
        min_zoom=0, max_zoom=16, opaque=True, bulk_ok=True,
        rate_limit=10.0, max_workers=6,
    ),
    TileSource(
        id="usgs_imagery",
        name="USGS Imagery Only",
        kind="xyz",
        url="https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
        attribution="USGS The National Map",
        min_zoom=0, max_zoom=16, opaque=True, bulk_ok=True,
        rate_limit=10.0, max_workers=6,
    ),
    TileSource(
        id="usgs_shaded_relief",
        name="USGS Shaded Relief",
        kind="xyz",
        url="https://basemap.nationalmap.gov/arcgis/rest/services/USGSShadedReliefOnly/MapServer/tile/{z}/{y}/{x}",
        attribution="USGS The National Map",
        min_zoom=0, max_zoom=16, opaque=True, bulk_ok=True,
        rate_limit=10.0, max_workers=6,
    ),
    TileSource(
        id="usfs_mvum",
        name="USFS MVUM (roads/trails, unlabeled)",
        kind="arcgis_export",
        url="https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MVUM_01/MapServer",
        attribution="USDA Forest Service",
        min_zoom=11, max_zoom=16, opaque=False, bulk_ok=True,
        rate_limit=4.0, max_workers=3,
        export_params={"layers": "show:1,2"},
        notes="Dynamic (uncached) service: one render request per tile. Transparent overlay; "
              "stack it on a base layer. Only the MVUM roads/trails layers (ids 1,2) are requested: "
              "the service's 'Status' layers would otherwise print 'No Travel Data' notices at "
              "zooms <= 10, and its 'Visitor Map Symbology' group draws the same roads a second "
              "time. The MVUM group is scale-limited to 1:300k, i.e. nothing renders below zoom 11.",
    ),
    TileSource(
        id="usfs_mvum_labeled",
        name="USFS MVUM (labeled)",
        kind="arcgis_export",
        url="https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MVUM_02/MapServer",
        attribution="USDA Forest Service",
        min_zoom=11, max_zoom=16, opaque=False, bulk_ok=True,
        rate_limit=4.0, max_workers=3,
        export_params={"layers": "show:1,2"},
        notes="Same as usfs_mvum but with road/trail labels (layers 1,2 only; see usfs_mvum).",
    ),
    TileSource(
        id="usfs_trails",
        name="USFS National Forest System trails",
        kind="arcgis_export",
        url="https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_TrailNFSPublish_01/MapServer",
        attribution="USDA Forest Service",
        min_zoom=9, max_zoom=16, opaque=False, bulk_ok=True,
        rate_limit=4.0, max_workers=3,
        notes="Transparent overlay of NFS trails (all, not only motorized). Dynamic service.",
    ),
    TileSource(
        id="usfs_roads",
        name="USFS roads (basic)",
        kind="arcgis_export",
        url="https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_RoadBasic_01/MapServer",
        attribution="USDA Forest Service",
        min_zoom=9, max_zoom=16, opaque=False, bulk_ok=True,
        rate_limit=4.0, max_workers=3,
        notes="Transparent overlay of Forest Service system roads. Dynamic service.",
    ),
    # FSTopo: the EDW services directory has no FSTopo map service (checked
    # 2026-10-17). FSTopo quads are published as GeoPDFs, which the Voyager Pro
    # can import directly, so they're not a tile source here.
    TileSource(
        id="opentopomap",
        name="OpenTopoMap",
        kind="xyz",
        url="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors, SRTM | © OpenTopoMap (CC-BY-SA)",
        min_zoom=0, max_zoom=17, opaque=True, bulk_ok=True,
        subdomains="abc",
        rate_limit=2.0, max_workers=2,
        notes="Community-funded server with limited capacity. Rate-limited to 2 req/s here; "
              "keep export areas modest and let the disk cache do the work on re-exports.",
    ),
    TileSource(
        id="osm",
        name="OpenStreetMap standard (preview only)",
        kind="xyz",
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors",
        min_zoom=0, max_zoom=19, opaque=True, bulk_ok=False,
        rate_limit=2.0, max_workers=2,
        notes="OSMF tile usage policy prohibits bulk download. Available for on-screen preview "
              "only; the exporter refuses it.",
    ),
]


def user_sources_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    return Path(base) / "voymap" / "sources.json"


_FIELDS = set(TileSource.__dataclass_fields__)


def load_user_sources(path: Optional[Path] = None) -> list[TileSource]:
    """Sources from the user's JSON file (unknown keys ignored, bad entries skipped)."""
    p = path or user_sources_path()
    out: list[TileSource] = []
    if not p.exists():
        return out
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"warning: could not read {p}: {e}")
        return out
    for d in data if isinstance(data, list) else []:
        try:
            d = {k: v for k, v in d.items() if k in _FIELDS}
            d["user"] = True
            src = TileSource(**d)
            if src.validate():
                print(f"warning: skipping source {src.id!r}: {'; '.join(src.validate())}")
                continue
            out.append(src)
        except Exception as e:  # noqa: BLE001
            print(f"warning: skipping bad source entry: {e}")
    return out


def save_user_sources(sources: list[TileSource], path: Optional[Path] = None) -> Path:
    p = path or user_sources_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = []
    for s in sources:
        d = s.to_dict()
        d.pop("user", None)
        data.append(d)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def load_sources(include_user: bool = True) -> dict[str, TileSource]:
    sources = {s.id: s for s in BUILTIN}
    if include_user:
        for src in load_user_sources():
            sources[src.id] = src       # a user entry with a built-in id overrides it
    return sources


def write_user_sources_template(path: Optional[Path] = None) -> Path:
    """Write an example file users can edit by hand (the app's Add source dialog is the easier route)."""
    path = path or user_sources_path()
    if path.exists():
        return path
    example = TileSource(id="example_xyz", name="Example XYZ source", kind="xyz",
                         url="https://tiles.example.com/{z}/{x}/{y}.png", attribution="Example",
                         min_zoom=0, max_zoom=16, opaque=True, notes="Edit or delete this entry.")
    return save_user_sources([example], path)
