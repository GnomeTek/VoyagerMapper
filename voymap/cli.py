"""Command-line interface.

  voymap export  --bbox W S E N --zooms 10-14 --layers usgs_topo,usfs_mvum@0.9 \
                 --gpx ride.gpx -o ride.mbtiles --name "Cinder Hills"
  voymap inspect ride.mbtiles
  voymap sources
  voymap estimate --bbox ... --zooms ...
  voymap gui
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .export import ExportJob, ExportSpec, LayerSpec, Progress
from .features import features_bbox, parse_gpx, load_geojson
from .mbtiles import format_inspection, inspect
from .sources import load_sources
from .tilemath import BBox


def _parse_zooms(s: str) -> tuple[int, int]:
    if "-" in s:
        a, b = s.split("-", 1)
        return int(a), int(b)
    z = int(s)
    return z, z


def _parse_layers(s: str) -> list[LayerSpec]:
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "@" in part:
            sid, op = part.split("@", 1)
            op = float(op)
            op = op / 100 if op > 1 else op
        else:
            sid, op = part, 1.0
        out.append(LayerSpec(sid, op))
    return out


def _progress_printer(p: Progress):
    pct = 100 * p.done / p.total if p.total else 0
    sys.stderr.write(f"\r[{p.phase:<22s}] {pct:5.1f}%  {p.message:<50s}  err={p.errors} miss={p.missing}")
    sys.stderr.flush()
    if p.phase in ("done", "error", "cancelled"):
        sys.stderr.write("\n")


def cmd_export(a):
    feats = []
    for g in a.gpx or []:
        feats += parse_gpx(g)
    for g in a.geojson or []:
        feats += load_geojson(g)
    if a.bbox:
        bbox = BBox(*a.bbox)
    else:
        fb = features_bbox(feats)
        if fb is None:
            sys.exit("need --bbox or at least one --gpx/--geojson to derive the area")
        pad = a.pad
        bbox = fb.buffered_deg(pad, pad)
    zmin, zmax = _parse_zooms(a.zooms)
    spec = ExportSpec(
        output=Path(a.output), name=a.name or Path(a.output).stem, bbox=bbox,
        zmin=zmin, zmax=zmax, layers=_parse_layers(a.layers), features=feats,
        encoding=a.encoding, jpeg_quality=a.jpeg_quality, png_colors=a.png_colors, refresh=a.refresh,
        spec_extras=a.spec_extras, skip_empty=not a.write_empty,
        cache_dir=Path(a.cache_dir) if a.cache_dir else None,
    )
    spec.theme = a.theme
    spec.snap_zoom = None if a.no_snap else a.snap_zoom
    spec.trim_coarse = a.trim_coarse
    est = spec.estimate()
    eb = spec.effective_bbox()
    print(f"area {bbox.as_tuple()}", file=sys.stderr)
    if eb != bbox:
        print(f"snapped to z{min(max(spec.snap_zoom, zmin), zmax)} tile grid: {eb.as_tuple()}", file=sys.stderr)
    print(f"zooms {zmin}-{zmax}  ~{est['tiles']} tiles  ~{est['bytes'] / 1e6:.0f} MB", file=sys.stderr)
    if a.dry_run:
        return
    specs = [spec]
    if a.both_themes:
        specs = [spec.variant("day", "_day"), spec.variant("night", "_night")]
    for s in specs:
        print(f"--- {s.theme}: {s.output}", file=sys.stderr)
        job = ExportJob(s, progress=_progress_printer)
        try:
            job.run()
        except KeyboardInterrupt:
            job.cancel()
            sys.exit("\ncancelled")
        print(format_inspection(inspect(s.output)))


def cmd_inspect(a):
    print(format_inspection(inspect(a.path)))


def cmd_sources(a):
    for s in load_sources().values():
        flag = "" if s.verified else "  [UNVERIFIED URL]"
        bulk = "" if s.bulk_ok else "  [preview only]"
        print(f"{s.id:20s} z{s.min_zoom}-{s.max_zoom}  {'base' if s.opaque else 'overlay':7s} {s.name}{flag}{bulk}")
        if s.notes:
            print(f"{'':20s} {s.notes}")


def cmd_estimate(a):
    bbox = BBox(*a.bbox)
    zmin, zmax = _parse_zooms(a.zooms)
    snapped = bbox.snapped(min(max(a.snap_zoom, zmin), zmax)) if not a.no_snap else bbox
    if snapped != bbox:
        print(f"snapped area: {snapped.as_tuple()}")
    for z in range(zmin, zmax + 1):
        print(f"z{z:<3d} {snapped.tile_count(z):8d} tiles")
    n = snapped.total_tiles(zmin, zmax)
    print(f"total {n} tiles, ~{n * 30_000 / 1e6:.0f} MB at ~30 KB/tile")


def cmd_gui(a):
    from .gui.app import main as gui_main
    gui_main()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="voymap", description="Build MBTiles maps for the Trail Tech Voyager Pro")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="build an MBTiles file")
    e.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"))
    e.add_argument("--pad", type=float, default=0.01, help="degrees of padding when the area comes from GPX")
    e.add_argument("--zooms", required=True, help="e.g. 10-14")
    e.add_argument("--layers", default="usgs_topo", help="comma list, optional @opacity, e.g. usgs_topo,usfs_mvum@0.9")
    e.add_argument("--gpx", action="append", help="GPX file(s) to burn into the tiles")
    e.add_argument("--geojson", action="append", help="GeoJSON feature file(s) (voymap project features)")
    e.add_argument("-o", "--output", required=True)
    e.add_argument("--name", help="map name shown on the device (default: output filename stem)")
    e.add_argument("--encoding", choices=("png8", "png", "jpeg"), default="png8")
    e.add_argument("--jpeg-quality", type=int, default=80)
    e.add_argument("--png-colors", type=int, default=256, help="palette size for png8 (CalTopo: 256)")
    e.add_argument("--theme", choices=("day", "night", "dim"), default="day",
                   help="tile colour theme; night = inverted luminance with hues kept")
    e.add_argument("--both-themes", action="store_true",
                   help="write <output>_day.mbtiles and <output>_night.mbtiles (second pass uses the cache)")
    e.add_argument("--snap-zoom", type=int, default=11,
                   help="snap the area outward to this zoom's tile grid so all zooms >= it cover the same "
                        "rectangle (CalTopo snaps only at its max zoom); clamped to the zoom range")
    e.add_argument("--no-snap", action="store_true", help="no snapping: each zoom takes the tiles touching the bbox")
    e.add_argument("--trim-coarse", action="store_true",
                   help="make the part of coarse-zoom tiles outside the snapped area transparent (untested on device)")
    e.add_argument("--refresh", action="store_true", help="ignore the tile cache and re-download")
    e.add_argument("--spec-extras", action="store_true", help="also write minzoom/maxzoom/center metadata (untested on device)")
    e.add_argument("--write-empty", action="store_true", help="write grey tiles where no layer has data")
    e.add_argument("--cache-dir")
    e.add_argument("--dry-run", action="store_true")
    e.set_defaults(func=cmd_export)

    i = sub.add_parser("inspect", help="print schema/metadata/zoom stats of an MBTiles file")
    i.add_argument("path")
    i.set_defaults(func=cmd_inspect)

    s = sub.add_parser("sources", help="list tile sources")
    s.set_defaults(func=cmd_sources)

    es = sub.add_parser("estimate", help="tile count for an area/zoom range")
    es.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("W", "S", "E", "N"))
    es.add_argument("--zooms", required=True)
    es.add_argument("--snap-zoom", type=int, default=11)
    es.add_argument("--no-snap", action="store_true")
    es.set_defaults(func=cmd_estimate)

    g = sub.add_parser("gui", help="launch the desktop app")
    g.set_defaults(func=cmd_gui)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
