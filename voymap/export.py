"""Export orchestration: area + zoom range + stacked sources + vector overlay
-> MBTiles. Pure Python; the GUI runs this in a worker thread and the CLI runs
it directly.

Progress is reported through a callback with a ``Progress`` snapshot so the
caller can draw a bar and show the current phase.
"""
from __future__ import annotations

import io
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

from .features import Feature
from .fetch import STATUS_OK, TileFetcher, default_cache_dir
from .mbtiles import MBTilesWriter, TileEncoder
from .overlay import OverlayRenderer
from .sources import TileSource, load_sources
from .themes import THEMES, apply_theme
from .tilemath import TILE_SIZE, BBox, tile_bounds

AVG_TILE_BYTES = 30_000  # observed on the CalTopo reference export


@dataclass
class LayerSpec:
    source_id: str
    opacity: float = 1.0      # 0..1


@dataclass
class ExportSpec:
    output: Path
    name: str
    bbox: BBox
    zmin: int
    zmax: int
    layers: list[LayerSpec]
    features: list[Feature] = field(default_factory=list)
    encoding: str = "png8"
    jpeg_quality: int = 80
    png_colors: int = 256     # palette size for png8
    polygon: Optional[list[tuple[float, float]]] = None  # optional clip ring (lon,lat)
    refresh: bool = False
    spec_extras: bool = False
    description: str = "N/A"
    cache_dir: Optional[Path] = None
    skip_empty: bool = True   # don't write tiles where every layer is missing
    theme: str = "day"        # "day" | "night" | "dim" (see themes.py)
    # Snap the area outward to the tile grid of this zoom so every zoom >= snap_zoom
    # covers exactly the same rectangle (CalTopo snaps only at its max zoom). None = no snapping.
    snap_zoom: Optional[int] = 11
    # For zooms below the snap zoom, make the part of each tile outside the snapped
    # area transparent so every zoom shows the same extent. UNTESTED on the device:
    # CalTopo never writes partially transparent tiles.
    trim_coarse: bool = False

    def effective_snap_zoom(self) -> Optional[int]:
        if self.snap_zoom is None:
            return None
        return min(max(self.snap_zoom, self.zmin), self.zmax)

    def effective_bbox(self) -> BBox:
        z = self.effective_snap_zoom()
        return self.bbox if z is None else self.bbox.snapped(z)

    def per_zoom_counts(self) -> list[tuple[int, int]]:
        bbox = self.effective_bbox()
        return [(z, bbox.tile_count(z)) for z in range(self.zmin, self.zmax + 1)]

    def variant(self, theme: str, suffix: str) -> "ExportSpec":
        """Copy of this spec with another theme and an output-name suffix."""
        from dataclasses import replace
        out = self.output.with_name(f"{self.output.stem}{suffix}{self.output.suffix}")
        return replace(self, theme=theme, output=out, name=f"{self.name} ({theme})")

    def tile_list(self) -> list[tuple[int, int, int]]:
        bbox = self.effective_bbox()
        tiles = [t for z in range(self.zmin, self.zmax + 1) for t in bbox.tiles(z)]
        if self.polygon:
            tiles = [t for t in tiles if _tile_touches_polygon(t, self.polygon)]
        return tiles

    def estimate(self) -> dict:
        n = len(self.tile_list())
        return {"tiles": n, "bytes": n * AVG_TILE_BYTES}


@dataclass
class Progress:
    phase: str                 # "fetch:<source>" | "render" | "done" | "error"
    done: int
    total: int
    message: str = ""
    errors: int = 0
    missing: int = 0
    written: int = 0
    bytes: int = 0
    # whole-export progress: fetch units (one per layer per tile) + render units (one per tile)
    overall_done: int = 0
    overall_total: int = 0
    step: str = ""             # e.g. "step 2 of 3" (layers + render)
    started_at: float = 0.0    # time.time() when the job started (for ETA)


ProgressCb = Callable[[Progress], None]


def _point_in_ring(lon: float, lat: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat):
            xint = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < xint:
                inside = not inside
        j = i
    return inside


def _tile_touches_polygon(t: tuple[int, int, int], ring: list[tuple[float, float]]) -> bool:
    x, y, z = t
    w, s, e, n = tile_bounds(x, y, z)
    cx, cy = (w + e) / 2, (s + n) / 2
    if any(_point_in_ring(px, py, ring) for px, py in ((cx, cy), (w, s), (w, n), (e, s), (e, n))):
        return True
    tb = BBox(w, s, e, n)
    return any(tb.contains(px, py) for px, py in ring)


class ExportJob:
    def __init__(self, spec: ExportSpec, sources: Optional[dict[str, TileSource]] = None,
                 progress: Optional[ProgressCb] = None):
        self.spec = spec
        self.sources = sources or load_sources()
        self.progress = progress or (lambda p: None)
        self.cancel_event = threading.Event()
        self.fetchers: dict[str, TileFetcher] = {}
        if spec.theme not in THEMES:
            raise ValueError(f"unknown theme {spec.theme!r}; choose one of {THEMES}")
        for layer in spec.layers:
            src = self.sources.get(layer.source_id)
            if src is None:
                raise ValueError(f"unknown source {layer.source_id!r}")
            if not src.bulk_ok:
                raise ValueError(f"source {src.id!r} is preview-only (its usage policy forbids bulk download)")
            self.fetchers[src.id] = TileFetcher(src, cache_dir=spec.cache_dir or default_cache_dir(),
                                                refresh=spec.refresh)

    def cancel(self):
        self.cancel_event.set()
        for f in self.fetchers.values():
            f.cancel()

    def run(self) -> Progress:
        import time
        spec = self.spec
        tiles = spec.tile_list()
        total = len(tiles)
        if total == 0:
            raise ValueError("no tiles in the selected area/zoom range")
        errors = missing = 0
        started = time.time()

        # Work plan, so the overall bar is exact: one unit per (layer, tile) fetch, one per tile render.
        plan = []
        for layer in spec.layers:
            src = self.fetchers[layer.source_id].source
            plan.append((src, [t for t in tiles if src.min_zoom <= t[2] <= src.max_zoom]))
        overall_total = sum(len(w) for _, w in plan) + total
        overall_base = 0
        n_steps = len(plan) + 1

        def report(phase, done, n, msg, step_i, **kw):
            self.progress(Progress(phase, done, n, msg, errors=errors, missing=missing,
                                   overall_done=overall_base + done, overall_total=overall_total,
                                   step=f"step {step_i} of {n_steps}", started_at=started, **kw))

        # Phase 1: fetch every layer into the cache (per-source pools + rate limits).
        for li, (src, wanted) in enumerate(plan, 1):
            fetcher = self.fetchers[src.id]
            done = 0

            def cb(res, _src=src, _n=len(wanted), _li=li):
                nonlocal done, errors
                done += 1
                if res.status == "error":
                    errors += 1
                if done % 25 == 0 or done == _n:
                    report(f"fetch:{_src.id}", done, _n, f"{_src.name}: {done}/{_n}", _li)

            report(f"fetch:{src.id}", 0, len(wanted), f"{src.name}: starting", li)
            fetcher.fetch_many(wanted, on_result=cb)
            overall_base += len(wanted)
            if self.cancel_event.is_set():
                return Progress("cancelled", done, total, "cancelled", errors=errors,
                                overall_done=overall_base, overall_total=overall_total, started_at=started)

        # Phase 2: composite + overlay + encode + write.
        renderer = OverlayRenderer(spec.features)
        encoder = TileEncoder(spec.encoding, spec.jpeg_quality, spec.png_colors)
        written = 0
        with MBTilesWriter(spec.output, spec.name, spec.effective_bbox(), encoder,
                           description=spec.description, spec_extras=spec.spec_extras,
                           attribution="; ".join(sorted({self.fetchers[l.source_id].source.attribution
                                                          for l in spec.layers}))) as writer:
            for i, (x, y, z) in enumerate(tiles, 1):
                if self.cancel_event.is_set():
                    break
                img = self._composite(x, y, z)
                ov = renderer.render_tile(x, y, z) if not renderer.is_empty() else None
                if img is None and ov is None:
                    missing += 1
                    if spec.skip_empty:
                        continue
                    img = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (235, 235, 235, 255))
                if img is None:
                    img = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (235, 235, 235, 255))
                img = apply_theme(img, spec.theme)
                if spec.trim_coarse and spec.effective_snap_zoom() is not None and z < spec.effective_snap_zoom():
                    img = _trim_outside(img, x, y, z, spec.effective_bbox())
                if ov is not None:
                    img = Image.alpha_composite(img, ov)
                writer.add_image_xyz(x, y, z, img)
                written += 1
                if i % 20 == 0 or i == total:
                    report("render", i, total, f"rendering {i}/{total}", n_steps, written=written, bytes=writer.bytes)
            final_bytes = writer.bytes
            if self.cancel_event.is_set():
                writer.abort()
        p = Progress("cancelled" if self.cancel_event.is_set() else "done", total, total,
                     f"wrote {written} tiles to {spec.output}", errors=errors, missing=missing,
                     written=written, bytes=final_bytes, overall_done=overall_total, overall_total=overall_total,
                     step=f"step {n_steps} of {n_steps}", started_at=started)
        self.progress(p)
        return p

    def _composite(self, x: int, y: int, z: int) -> Optional[Image.Image]:
        """Stack the layers for one tile. None if every layer is missing."""
        out: Optional[Image.Image] = None
        for layer in self.spec.layers:
            fetcher = self.fetchers[layer.source_id]
            src = fetcher.source
            if not (src.min_zoom <= z <= src.max_zoom):
                continue
            hit = fetcher.cache.get(z, x, y)
            if not hit or hit[0] != STATUS_OK or not hit[1]:
                continue
            try:
                tile = Image.open(io.BytesIO(hit[1])).convert("RGBA")
            except Exception:  # noqa: BLE001 — corrupt cache entry
                continue
            if tile.size != (TILE_SIZE, TILE_SIZE):
                tile = tile.resize((TILE_SIZE, TILE_SIZE), Image.LANCZOS)
            if layer.opacity < 1.0:
                a = tile.getchannel("A").point(lambda v: int(v * layer.opacity))
                tile.putalpha(a)
            if out is None:
                out = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (255, 255, 255, 255))
            out = Image.alpha_composite(out, tile)
        return out


def _trim_outside(img: Image.Image, x: int, y: int, z: int, bbox: BBox) -> Image.Image:
    """Set alpha to 0 for the part of tile (x, y, z) that lies outside bbox."""
    from .tilemath import lonlat_to_pixel
    ox, oy = x * TILE_SIZE, y * TILE_SIZE
    px0, py0 = lonlat_to_pixel(bbox.west, bbox.north, z)
    px1, py1 = lonlat_to_pixel(bbox.east, bbox.south, z)
    l = max(0, int(round(px0 - ox))); t = max(0, int(round(py0 - oy)))
    r = min(TILE_SIZE, int(round(px1 - ox))); b = min(TILE_SIZE, int(round(py1 - oy)))
    if l <= 0 and t <= 0 and r >= TILE_SIZE and b >= TILE_SIZE:
        return img
    mask = Image.new("L", (TILE_SIZE, TILE_SIZE), 0)
    if r > l and b > t:
        mask.paste(255, (l, t, r, b))
    out = img.copy()
    alpha = out.getchannel("A")
    from PIL import ImageChops
    out.putalpha(ImageChops.multiply(alpha, mask))
    return out
