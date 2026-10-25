# VoyagerMapper

Lightweight Python map building application for the Trail Tech Voyager navigation units: build offline MBTiles maps and GPX files for the Voyager Pro from public tile sources.

[![tests](https://github.com/GnomeTek/VoyagerMapper/actions/workflows/ci.yml/badge.svg)](https://github.com/GnomeTek/VoyagerMapper/actions/workflows/ci.yml)

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

**Windows installer** — download `voymap-setup-<version>.exe` from the
[Releases page](https://github.com/GnomeTek/VoyagerMapper/releases) and run it.
No Python needed. A portable zip is there too. The installer is not
code-signed, so Windows SmartScreen will show a "Windows protected your PC"
warning the first time: click *More info → Run anyway*. The `SHA256SUMS.txt`
alongside the download lets you verify the file.

**From source** — Python 3.10 or newer.

```bash
git clone https://github.com/GnomeTek/VoyagerMapper.git
cd VoyagerMapper
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
2. **Tracks & waypoints**: `File → Import GPX…`, or draw. Click a feature to
   set its name, color, width and label. "Min zoom" hides a feature on tiles
   below that zoom.

   Drawing a track ("Draw track" button, or the ✎ button on the map). A small
   toolbar at the top of the map shows the state and point count while you draw:
   * left-click adds a point;
   * right-click, Space, or the Pause button pauses — the mouse is free to pan
     and zoom — and any of the same resumes;
   * double right-click, Enter, or the Finish button finishes; Esc or Cancel
     cancels.
   * To add points to a track you already finished, select it and click
     "Extend track": drawing continues from its last point.

   Waypoints and areas use the map toolbar (marker and polygon tools); the
   edit / drag / delete tools on that toolbar work on everything.
3. **Export**: click "Draw export rectangle" and drag on the map, or "Area =
   tracks extent". Set the zoom range. The label shows the tile count and an
   approximate size (about 30 KB per tile, measured on a CalTopo export).
   Click "Export MBTiles…".
4. Copy the `.mbtiles` to the Voyager Pro microSD card. On the device: map
   screen → **CUSTOM MAPS** → **IMPORT A NEW FILE**.

**Advanced options** (fold-out in the Export panel) hold the rest: JPEG
quality, png8 palette size, trimming coarse-zoom tiles to the area
(transparent outside; untested on the device), grey tiles where no layer has
data, the extra metadata keys, forcing a re-download, the padding used by
"Area = tracks extent", the MBTiles description, and the tile cache (location,
open, clear). Every option the command line has is in the app.

**Add source…** (Layers panel) adds your own tile server: an XYZ URL template
(`{z}/{x}/{y}`, optional `{s}` subdomains) or a dynamic ArcGIS MapServer. Set
the zoom range, base-map vs overlay, and a rate limit, then **Test** fetches
one tile at the current view before you save. Your sources are marked ★ in the
list, and clicking a ★ name then **Edit…** / **Remove** changes or drops it;
the list updates live. They're stored in `~/.config/voymap/sources.json`
(`%USERPROFILE%\.config\voymap\sources.json` on Windows), which **Source
details…** also lists along with every built-in source's URL and notes.
Check a server's usage terms before bulk-downloading from it.

`File → Save project` stores features, layers and all export settings as
`*.voymap.json` so you can rebuild later.

If the map area stays blank, click **Test sources** in the Layers panel: it
downloads one tile for each checked layer through Python and reports the
HTTP result, which separates a page problem from a network or proxy problem.

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

## Export area and zoom levels: why extents differ per zoom

A tile at zoom *z* is a fixed square on the Web Mercator grid, so an export can
only include whole tiles. If each zoom level takes "the tiles that touch my
rectangle", the covered extent is slightly different at every zoom — up to one
tile wider at each level. CalTopo's exports behave this way too: the reference
file's bounds are tile edges at its maximum zoom only.

voymap's default is **snap area to tile grid at zoom 11**: the rectangle is
expanded outward to the zoom-11 tile edges, so every zoom from 11 up covers
exactly the same area. Coarser zooms (8–10) still overshoot by whole tiles,
which is harmless — those tiles are few and small. The dotted outline on the
map shows the snapped extent, and the tile estimate is computed from it.

* Lower snap zoom → bigger area, identical over more zoom levels, more tiles.
  Snapping at the minimum zoom makes every level identical, but at zoom 8 a
  tile is ~130 km wide, so that is rarely what you want.
* Higher snap zoom (or snapping off) → tighter area, extents vary per zoom.

## Zoom levels

Zoom 13 ≈ 19 m/pixel at 35° N; zoom 14 ≈ 9.6 m/pixel; zoom 15 ≈ 4.8 m/pixel.
Each zoom step multiplies the tile count by four. The CalTopo reference export
stops at zoom 13.

## Tile sources and their rules

| id                   | type    | notes |
|----------------------|---------|-------|
| `usgs_topo`          | base    | Public domain. Cache advertised to level 17; default max 16. |
| `usgs_imagery`, `usgs_imagery_topo`, `usgs_shaded_relief` | base | Public domain. |
| `usfs_mvum`, `usfs_mvum_labeled` | overlay | Dynamic ArcGIS service: one render request per tile, so it is slow. Rate-limited to 4 req/s. Only the MVUM roads/trails layers are requested; the service's status layers (the "No Travel Data" notices) and duplicate visitor-map symbology are excluded. Renders from zoom 11 up (the service is scale-limited to 1:300k). |
| `usfs_roads`, `usfs_trails` | overlay | Same service type as MVUM. |
| `opentopomap`        | base    | Community server, CC-BY-SA. Rate-limited to 2 req/s. Keep areas modest. |
| `osm`                | —       | Defined in the registry but **not offered**: the OSM Foundation tile policy forbids bulk download. |

FSTopo is not available as a tile service on the USFS EDW server (checked
2026-10-17). FSTopo quads are published as GeoPDFs, which the Voyager Pro can
import directly.

To add a source use **Add source…** in the app, or edit
`~/.config/voymap/sources.json` by hand (a list of objects with the fields
printed by `voymap sources`; an entry whose `id` matches a built-in one
overrides it, which is how to raise a built-in's `max_zoom`, for example).

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

## Building the installer

Releases are built by GitHub Actions (`.github/workflows/release.yml`): pushing
a tag `vX.Y.Z` that matches `voymap.__version__` runs the tests, freezes the
app with PyInstaller (`packaging/voymap.spec`), wraps it with Inno Setup
(`packaging/voymap.iss`), and attaches the installer, a portable zip and
checksums to a GitHub Release. To build locally on Windows, install
[Inno Setup 6](https://jrsoftware.org/isinfo.php) and run `build_installer.bat`.

To cut a release: bump `__version__` in `voymap/__init__.py`, commit, then
`git tag v0.2.0 && git push --tags`.

## License

GPL-3.0-or-later. Bundled third-party code: Leaflet (BSD-2-Clause) and
Leaflet-Geoman free (MIT) under `voymap/gui/web/vendor/`; Qt/PySide6 (LGPL-3.0)
in the installer. Map data and tiles belong to their providers; see the
attribution each source carries.

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
  gui/          PySide6 window, Leaflet page served over loopback HTTP, QWebChannel bridge
tests/          offline tests (tile math vs. the reference export, MBTiles round-trip)
```
