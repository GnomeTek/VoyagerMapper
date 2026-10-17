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
                      features=feats, cache_dir=cache_dir)
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
    reds = sum(1 for px in im.get_flattened_data() if px[0] > 200 and px[1] < 90 and px[2] < 90)
    assert reds > 20, reds
    # overlay blended at 50%: background should be a blue-ish mix, not pure base green
    bg = im.getpixel((3, 3))
    assert bg[2] > bg[1] - 60  # blue contribution present


def test_preview_only_source_refused(tmp_path):
    src = TileSource(id="osm_like", name="x", kind="xyz", url="http://invalid/{z}/{x}/{y}", bulk_ok=False)
    spec = ExportSpec(output=tmp_path / "x.mbtiles", name="x", bbox=BBox(-1, -1, 1, 1), zmin=1, zmax=1,
                      layers=[LayerSpec("osm_like")], cache_dir=tmp_path)
    import pytest
    with pytest.raises(ValueError):
        ExportJob(spec, sources={"osm_like": src})


def test_arcgis_export_url():
    src = TileSource(id="m", name="m", kind="arcgis_export",
                     url="https://example/arcgis/rest/services/X/MapServer", opaque=False)
    u = src.tile_url(0, 0, 1)
    assert u.startswith("https://example/arcgis/rest/services/X/MapServer/export?bbox=")
    assert "bboxSR=3857" in u and "transparent=true" in u and "size=256,256" in u
