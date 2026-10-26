"""Offline end-to-end: synthetic tiles pre-seeded into the cache, GPX overlay,
MBTiles written and verified against the reference CalTopo layout."""
import io
import sqlite3
from pathlib import Path

from PIL import Image

from voymap.export import ExportJob, ExportSpec, LayerSpec
from voymap.features import Feature, parse_gpx, write_gpx
from voymap.fetch import STATUS_OK, TileCache
from voymap.mbtiles import inspect, read_tile_xyz
from voymap.sources import TileSource
from voymap.tilemath import BBox, lonlat_to_pixel, TILE_SIZE

REF_META_KEYS = ["name", "type", "version", "description", "format", "created", "bounds"]


def _seed(cache_dir: Path, src: TileSource, bbox: BBox, zmin, zmax, color):
    cache = TileCache(cache_dir / f"{src.id}.sqlite")
    for z in range(zmin, zmax + 1):
        for x, y, _ in bbox.tiles(z):
            im = Image.new("RGBA", (256, 256), color)
            buf = io.BytesIO()
            im.save(buf, "PNG")
            cache.put(z, x, y, STATUS_OK, buf.getvalue())


def test_export_matches_reference_layout(tmp_path):
    bbox = BBox(-111.70, 35.15, -111.60, 35.25)  # ~10 km box near Flagstaff
    base = TileSource(id="fake_base", name="fake", kind="xyz", url="http://invalid/{z}/{x}/{y}",
                      min_zoom=0, max_zoom=14, opaque=True)
    over = TileSource(id="fake_over", name="fake overlay", kind="xyz", url="http://invalid/{z}/{x}/{y}",
                      min_zoom=0, max_zoom=14, opaque=False)
    cache_dir = tmp_path / "cache"
    _seed(cache_dir, base, bbox, 10, 12, (200, 220, 200, 255))
    _seed(cache_dir, over, bbox, 10, 12, (0, 0, 255, 128))

    # a GPX track across the box + a waypoint
    gpx = tmp_path / "ride.gpx"
    write_gpx([
        Feature("t1", "line", "Loop", [[(-111.69, 35.16), (-111.65, 35.20), (-111.61, 35.24)]],
                style={"color": "#ff0000", "width": 4}),
        Feature("w1", "point", "Camp", [[(-111.65, 35.20)]]),
    ], gpx)
    feats = parse_gpx(gpx)
    assert {f.kind for f in feats} == {"line", "point"}
    for f in feats:
        if f.kind == "line":
            f.style = {"color": "#ff0000", "width": 4}

    out = tmp_path / "out.mbtiles"
    spec = ExportSpec(output=out, name="Test Map", bbox=bbox, zmin=10, zmax=12,
                      layers=[LayerSpec("fake_base"), LayerSpec("fake_over", 0.5)],
                      features=feats, cache_dir=cache_dir, snap_zoom=None)
    job = ExportJob(spec, sources={"fake_base": base, "fake_over": over})
    p = job.run()
    assert p.phase == "done"
    assert p.written == bbox.total_tiles(10, 12) == spec.estimate()["tiles"]

    info = inspect(out)
    assert list(info["metadata"].keys()) == REF_META_KEYS
    assert info["metadata"]["format"] == "png" and info["metadata"]["type"] == "baselayer"
    assert [n for t, n in info["schema"] if t == "index"] == ["idx_x", "idx_y", "idx_z"]
    assert info["sample_tile"]["mode"] == "P" and info["sample_tile"]["size"] == (256, 256)

    # TMS row check: the tile containing the waypoint must exist under its flipped row.
    from voymap.tilemath import lonlat_to_tile
    x, y = lonlat_to_tile(-111.65, 35.20, 12)
    data = read_tile_xyz(out, x, y, 12)
    assert data is not None
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    # the red track passes through this tile: look for strongly red pixels
    reds = sum(n for n, px in im.getcolors(256 * 256) if px[0] > 200 and px[1] < 90 and px[2] < 90)
    assert reds > 20, reds
    # overlay blended at 50%: background should be a blue-ish mix, not pure base green
    bg = im.getpixel((3, 3))
    assert bg[2] > bg[1] - 60  # blue contribution present


def test_preview_only_source_refused(tmp_path):
    src = TileSource(id="osm_like", name="x", kind="xyz", url="http://invalid/{z}/{x}/{y}", bulk_ok=False)
    spec = ExportSpec(output=tmp_path / "x.mbtiles", name="x", bbox=BBox(-1, -1, 1, 1), zmin=1, zmax=1,
                      layers=[LayerSpec("osm_like")], cache_dir=tmp_path, snap_zoom=None)
    import pytest
    with pytest.raises(ValueError):
        ExportJob(spec, sources={"osm_like": src})


def test_arcgis_export_url():
    src = TileSource(id="m", name="m", kind="arcgis_export",
                     url="https://example/arcgis/rest/services/X/MapServer", opaque=False)
    u = src.tile_url(0, 0, 1)
    assert u.startswith("https://example/arcgis/rest/services/X/MapServer/export?bbox=")
    assert "bboxSR=3857" in u and "transparent=true" in u and "size=256,256" in u


def test_theme_pair_export(tmp_path):
    from voymap.themes import apply_theme
    bbox = BBox(-111.66, 35.19, -111.64, 35.21)
    base = TileSource(id="fb", name="fb", kind="xyz", url="http://invalid/{z}/{x}/{y}", max_zoom=14)
    cache_dir = tmp_path / "cache"
    _seed(cache_dir, base, bbox, 12, 12, (255, 255, 255, 255))          # white "paper"
    spec = ExportSpec(output=tmp_path / "ride.mbtiles", name="Ride", bbox=bbox, zmin=12, zmax=12,
                      layers=[LayerSpec("fb")], cache_dir=cache_dir, snap_zoom=None)
    for s in (spec.variant("day", "_day"), spec.variant("night", "_night")):
        ExportJob(s, sources={"fb": base}).run()
    assert (tmp_path / "ride_day.mbtiles").exists() and (tmp_path / "ride_night.mbtiles").exists()
    from voymap.tilemath import lonlat_to_tile
    x, y = lonlat_to_tile(-111.65, 35.20, 12)
    day = Image.open(io.BytesIO(read_tile_xyz(tmp_path / "ride_day.mbtiles", x, y, 12))).convert("RGB")
    night = Image.open(io.BytesIO(read_tile_xyz(tmp_path / "ride_night.mbtiles", x, y, 12))).convert("RGB")
    assert day.getpixel((5, 5)) == (255, 255, 255)
    assert max(night.getpixel((5, 5))) < 30            # white paper -> near black
    assert inspect(tmp_path / "ride_night.mbtiles")["metadata"]["name"] == "Ride (night)"
    # hue preservation: pure blue water stays blue-ish after night transform
    blue = Image.new("RGBA", (4, 4), (40, 120, 220, 255))
    r, g, b = apply_theme(blue, "night").convert("RGB").getpixel((0, 0))
    assert b > r and b > g


def test_trim_coarse_makes_outside_transparent(tmp_path):
    bbox = BBox(-111.66, 35.19, -111.64, 35.21)
    base = TileSource(id="fb2", name="fb", kind="xyz", url="http://invalid/{z}/{x}/{y}", max_zoom=14)
    cache_dir = tmp_path / "cache"
    spec = ExportSpec(output=tmp_path / "t.mbtiles", name="T", bbox=bbox, zmin=9, zmax=12,
                      layers=[LayerSpec("fb2")], cache_dir=cache_dir, snap_zoom=12, trim_coarse=True)
    _seed(cache_dir, base, spec.effective_bbox(), 9, 12, (255, 255, 255, 255))
    ExportJob(spec, sources={"fb2": base}).run()
    from voymap.tilemath import lonlat_to_tile
    x, y = lonlat_to_tile(-111.65, 35.20, 9)
    im = Image.open(io.BytesIO(read_tile_xyz(tmp_path / "t.mbtiles", x, y, 9))).convert("RGBA")
    alphas = {px[3] for _, px in im.getcolors(256 * 256)}
    assert 0 in alphas and 255 in alphas            # partly transparent, partly opaque
    x, y = lonlat_to_tile(-111.65, 35.20, 12)        # at/above the snap zoom: untouched
    im = Image.open(io.BytesIO(read_tile_xyz(tmp_path / "t.mbtiles", x, y, 12))).convert("RGBA")
    assert im.getchannel("A").getextrema() == (255, 255)


def test_overall_progress_is_monotonic_and_exact(tmp_path):
    bbox = BBox(-111.66, 35.19, -111.64, 35.21)
    a = TileSource(id="pa", name="a", kind="xyz", url="http://invalid/{z}/{x}/{y}", max_zoom=14)
    b = TileSource(id="pb", name="b", kind="xyz", url="http://invalid/{z}/{x}/{y}", max_zoom=14, opaque=False)
    cache_dir = tmp_path / "cache"
    spec = ExportSpec(output=tmp_path / "p.mbtiles", name="P", bbox=bbox, zmin=10, zmax=12,
                      layers=[LayerSpec("pa"), LayerSpec("pb")], cache_dir=cache_dir, snap_zoom=None)
    _seed(cache_dir, a, bbox, 10, 12, (200, 200, 200, 255)); _seed(cache_dir, b, bbox, 10, 12, (0, 0, 255, 100))
    seen = []
    p = ExportJob(spec, sources={"pa": a, "pb": b}, progress=seen.append).run()
    n = len(spec.tile_list())
    assert p.overall_total == 3 * n                      # 2 fetch layers + 1 render per tile
    assert p.overall_done == p.overall_total
    dones = [q.overall_done for q in seen]
    assert dones == sorted(dones)                        # never goes backwards
    assert {q.step for q in seen} == {"step 1 of 3", "step 2 of 3", "step 3 of 3"}


def test_export_is_atomic(tmp_path):
    bbox = BBox(-111.66, 35.19, -111.64, 35.21)
    src = TileSource(id="at", name="at", kind="xyz", url="http://invalid/{z}/{x}/{y}", max_zoom=14)
    cache_dir = tmp_path / "cache"
    _seed(cache_dir, src, bbox, 12, 12, (255, 255, 255, 255))
    out = tmp_path / "a.mbtiles"
    out.write_bytes(b"previous good export")
    spec = ExportSpec(output=out, name="A", bbox=bbox, zmin=12, zmax=12,
                      layers=[LayerSpec("at")], cache_dir=cache_dir, snap_zoom=None)

    # cancelled mid-render: previous file intact, no .part left
    job = ExportJob(spec, sources={"at": src})
    job.progress = lambda p: job.cancel() if p.phase == "render" else None
    assert job.run().phase == "cancelled"
    assert out.read_bytes() == b"previous good export"
    assert not out.with_name(out.name + ".part").exists()

    # success replaces it
    assert ExportJob(spec, sources={"at": src}).run().phase == "done"
    assert inspect(out)["tile_count"] == bbox.total_tiles(12, 12)
    assert not out.with_name(out.name + ".part").exists()
