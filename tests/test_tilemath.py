import math

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
    import pytest
    with pytest.raises(ValueError):
        BBox(10, 0, 5, 1)
