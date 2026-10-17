# voymap — offline map builder for the Trail Tech Voyager Pro

voymap builds MBTiles map files (and GPX files) for the Trail Tech Voyager Pro
from public tile sources. It replaces the CalTopo export step: no Pro
subscription, no 8,000-tile cap per export, and a local tile cache so a
re-export of the same area costs no downloads.

What it does:

* Shows a map with selectable base layers and overlays (USGS Topo, USGS imagery,
  USFS MVUM, USFS roads and trails, OpenTopoMap).
* Lets you draw tracks, waypoints and areas, or import them from GPX.
* Burns those tracks and waypoints into the map tiles, so they show on the
  Voyager Pro map screen the way a CalTopo export does.
* Writes an MBTiles file in the same layout as a CalTopo export that is known to
  load on the device (see "Output format" below).
* Writes a GPX of your tracks and waypoints for the device's GPX import.

## Install

Python 3.10 or newer.

```bash
cd voymap
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[gui,dev]"   # Pillow, requests, PySide6, pytest
python -m pytest              # offline self-test (no network needed)
```

The GUI needs PySide6 with QtWebEngine (included in the PySide6 wheel). Leaflet
and Leaflet-Geoman are vendored under `voymap/gui/web/vendor/`, so the GUI does
not need internet except to fetch map tiles.

## Use the GUI

```bash
voymap gui          # or: python -m voymap gui
```

1. **Layers** panel: check one base map and any overlays. The slider sets
   opacity, in the preview and in the export.
2. **Tracks & waypoints**: `File → Import GPX…`, or use the draw buttons (the
   Leaflet toolbar on the map does the same). Click a feature to set its name,
   color, width and label. "Min zoom" hides a feature on tiles below that zoom.
3. **Export**: click "Draw export rectangle" and drag on the map, or "Area =
   tracks extent". Set the zoom range. The label shows the tile count and an
   approximate size (about 30 KB per tile, measured on a CalTopo export).
   Click "Export MBTiles…".
4. Copy the `.mbtiles` to the Voyager Pro microSD card. On the device: map
   screen → **CUSTOM MAPS** → **IMPORT A NEW FILE**.

`File → Save project` stores features, layers and export settings as
`*.voymap.json` so you can rebuild later.

## Use the command line

```bash
# tile count and size estimate
voymap estimate --bbox -111.80 35.10 -111.50 35.35 --zooms 8-14

# build: USGS topo base + MVUM overlay at 90 %, with a GPX burned in
voymap export --bbox -111.80 35.10 -111.50 35.35 --zooms 8-14 \
    --layers usgs_topo,usfs_mvum@0.9 --gpx cinder_hills.gpx \
    --name "Cinder Hills" -o cinder_hills.mbtiles

# area derived from the GPX (plus 0.01° padding)
voymap export --gpx ride.gpx --zooms 9-14 -o ride.mbtiles

# look inside any MBTiles file (works on CalTopo exports too)
voymap inspect ride.mbtiles

voymap sources      # list sources, zoom ranges and notes
```

## Output format

The writer copies the layout of a CalTopo export that loads on the Voyager Pro
(`AZCentral.mbtiles`, inspected 2026-10-17):

| item          | value                                                        |
|---------------|--------------------------------------------------------------|
| tables        | `metadata(name, value)`, `tiles(zoom_level, tile_column, tile_row, tile_data)` |
| indexes       | `idx_z`, `idx_x`, `idx_y` (single-column)                    |
| row order     | TMS (`tile_row = 2^z − 1 − y`)                               |
| tiles         | 256 × 256 PNG, palette mode, ≤ 256 colours (`png8`)          |
| metadata keys | `name, type=baselayer, version=1, description, format=png, created, bounds` only |

Two options deliberately deviate from that reference and are **untested on the
device**: `--encoding jpeg` (smaller files for imagery) and `--spec-extras`
(adds `minzoom`, `maxzoom`, `center`). Try them on a small export first.

## Day and night themes

The **Theme** control in the Export panel (or `--theme` on the command line)
recolours the base map tiles before tracks and waypoints are drawn, so your
features keep their chosen colours in every theme:

* `day` — unchanged.
* `night` — inverted luminance with hues kept (invert, then rotate hue 180°,
  then 90 % brightness): white paper becomes near-black, water stays blue,
  labels become light. The map preview applies the same look.
* `dim` — colours kept, 70 % brightness.

Tick **export a day + night pair** (or `--both-themes`) to write
`<name>_day.mbtiles` and `<name>_night.mbtiles` in one go; the night pass reads
tiles from the local cache, so it costs no extra downloads. On the device the
two show up as separate custom maps you switch between.

## Zoom levels

Zoom 13 ≈ 19 m/pixel at 35° N; zoom 14 ≈ 9.6 m/pixel; zoom 15 ≈ 4.8 m/pixel.
Each zoom step multiplies the tile count by four. The CalTopo reference export
stops at zoom 13.

## Tile sources and their rules

| id                   | type    | notes |
|----------------------|---------|-------|
| `usgs_topo`          | base    | Public domain. Cache advertised to level 17; default max 16. |
| `usgs_imagery`, `usgs_imagery_topo`, `usgs_shaded_relief` | base | Public domain. |
| `usfs_mvum`, `usfs_mvum_labeled` | overlay | Dynamic ArcGIS service: one render request per tile, so it is slow. Rate-limited to 4 req/s. |
| `usfs_roads`, `usfs_trails` | overlay | Same service type as MVUM. |
| `opentopomap`        | base    | Community server, CC-BY-SA. Rate-limited to 2 req/s. Keep areas modest. |
| `osm`                | base    | **Preview only.** The OSM Foundation tile policy forbids bulk download; the exporter refuses it. |

FSTopo is not available as a tile service on the USFS EDW server (checked
2026-10-17). FSTopo quads are published as GeoPDFs, which the Voyager Pro can
import directly.

To add or change a source, create `~/.config/voymap/sources.json` with a list of
objects in the shape printed by `voymap sources` (see `voymap/sources.py`).

The tile cache lives in `~/.cache/voymap/tiles/<source>.sqlite`. Delete a file
there to force a fresh download, or pass `--refresh`.

## Known gaps

* No GeoPDF export yet. CalTopo's GeoPDF route is still the way to get a
  "paper map" sheet onto the device.
* CalTopo's own rendered layers (its "MapBuilder" styles, slope shading and
  similar) are not available from any public tile server, so they cannot be
  reproduced here.
* The live tile endpoints were verified from their service directories, not by
  downloading tiles from this build environment. The first export on your
  machine is the real test; `voymap export --dry-run` and a small zoom range
  are a cheap way to start.
* The GUI was exercised offscreen (bridge, feature round-trip, export area,
  estimates), not with a visible window.

## Layout

```
voymap/
  tilemath.py   Web Mercator tile math, BBox → tile ranges (matches CalTopo's)
  sources.py    tile source registry (built-in + ~/.config/voymap/sources.json)
  fetch.py      throttled, cached tile fetcher (per-source SQLite cache)
  features.py   tracks/waypoints/polygons, GPX + GeoJSON I/O
  overlay.py    rasterizes features onto tiles (2x supersampled)
  mbtiles.py    MBTiles writer matched to the CalTopo reference; inspector
  themes.py     day / night / dim tile recolouring (+ matching CSS preview filter)
  export.py     export job: fetch → composite → overlay → encode → write
  cli.py        command line
  gui/          PySide6 window, Leaflet page, QWebChannel bridge
tests/          offline tests (tile math vs. the reference export, MBTiles round-trip)
```
