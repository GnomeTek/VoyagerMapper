import math

import pytest

from voymap.tilemath import (BBox, lonlat_to_pixel, lonlat_to_tile, pixel_to_lonlat,
                             tile_bounds, tile_bounds_3857, xyz_to_tms, tms_to_xyz)


def test_round_trip_pixel():
    for lon, lat in [(-111.65, 35.2), (0, 0), (179.9, -60), (-179.9, 80)]:
        for z in (0, 5, 13, 18):
            px, py = lonlat_to_pixel(lon, lat, z)
            lon2, lat2 = pixel_to_lonlat(px, py, z)
            assert abs(lon - lon2) < 1e-9 and abs(lat - lat2) < 1e-9


def test_known_tile():
    # Flagstaff, AZ at z13 — cross-checked against the standard slippy-map formula.
    lon, lat = -111.651, 35.198
    n = 2 ** 13
    x_ref = int((lon + 180) / 360 * n)
    y_ref = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    assert lonlat_to_tile(lon, lat, 13) == (x_ref, y_ref)


def test_tms_flip():
    assert xyz_to_tms(0, 3) == 7 and tms_to_xyz(7, 3) == 0
    assert tms_to_xyz(xyz_to_tms(1234, 13), 13) == 1234


def test_sample_export_bounds_match_caltopo_rows():
    # From the reference CalTopo export (AZCentral.mbtiles): bounds and the
    # TMS row ranges it actually contains per zoom.
    b = BBox(-113.37890625, 31.353636941500987, -111.1376953125, 37.020098201368135)
    expect = {6: (11, 12, 37, 39), 7: (23, 24, 75, 78), 13: (1516, 1566, 4848, 5003)}
    for z, (cmin, cmax, rmin, rmax) in expect.items():
        xmin, ymin, xmax, ymax = b.tile_range(z)
        assert (xmin, xmax) == (cmin, cmax), z
        assert (xyz_to_tms(ymax, z), xyz_to_tms(ymin, z)) == (rmin, rmax), z
    assert b.tile_count(13) == 7956
    assert b.total_tiles(6, 13) == 10697


def test_tile_bounds_consistency():
    w, s, e, n = tile_bounds(1516, 3187, 13)
    assert w < e and s < n
    xmin, ymin, xmax, ymax = tile_bounds_3857(0, 0, 0)
    assert abs(xmin + 20037508.34) < 1 and abs(ymax - 20037508.34) < 1


def test_bbox_validation():
    with pytest.raises(ValueError):
        BBox(10, 0, 5, 1)


def test_snapping_makes_extents_identical_across_zooms():
    b = BBox(-111.70, 35.15, -111.60, 35.25)
    sb = b.snapped(11)
    # the union of tiles at every zoom >= 11 is exactly the snapped rectangle
    for z in range(11, 16):
        xmin, ymin, xmax, ymax = sb.tile_range(z)
        w, _, _, n = tile_bounds(xmin, ymin, z)
        _, s, e, _ = tile_bounds(xmax, ymax, z)
        assert abs(w - sb.west) < 1e-9 and abs(e - sb.east) < 1e-9
        assert abs(s - sb.south) < 1e-9 and abs(n - sb.north) < 1e-9
    # without snapping, extents differ between zooms (this is what the user saw)
    ext = set()
    for z in range(11, 14):
        xmin, ymin, xmax, ymax = b.tile_range(z)
        ext.add((tile_bounds(xmin, ymin, z)[0], tile_bounds(xmax, ymax, z)[2]))
    assert len(ext) > 1


def test_caltopo_bounds_are_max_zoom_tile_edges():
    # The reference export's bounds are exactly tile edges at its max zoom (13), not at 11:
    # CalTopo snaps only at the finest level, so its coarser zooms overshoot by whole tiles.
    b = BBox(-113.37890625, 31.353636941500987, -111.1376953125, 37.020098201368135)
    s13 = b.snapped(13)
    assert all(abs(x - y) < 1e-9 for x, y in zip(s13.as_tuple(), b.as_tuple()))
    s11 = b.snapped(11)
    assert any(abs(x - y) > 1e-6 for x, y in zip(s11.as_tuple(), b.as_tuple()))


def test_bbox_from_dict_coerces_numbers_and_rejects_bad_input():
    b = BBox.from_dict({"west": -112, "south": "35.1", "east": -111.5, "north": 35.4, "ring": None})
    assert (b.west, b.south, b.east, b.north) == (-112.0, 35.1, -111.5, 35.4)
    bad = [
        {"west": "0);alert(1);(0", "south": 35, "east": -111, "north": 36},  # script in a project file
        {"west": -112, "south": 35, "east": -111},  # missing north
        {"west": None, "south": 35, "east": -111, "north": 36},
        {"west": [1], "south": 35, "east": -111, "north": 36},
        {"west": "nan", "south": 35, "east": -111, "north": 36},
        {"west": -111, "south": 35, "east": -112, "north": 36},  # west > east
        "not a mapping",
    ]
    for d in bad:
        with pytest.raises((KeyError, TypeError, ValueError)):
            BBox.from_dict(d)
