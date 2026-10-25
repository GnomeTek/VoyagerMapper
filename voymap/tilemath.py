"""Web Mercator (EPSG:3857) slippy-map tile math.

Conventions:
  * XYZ: x increases east, y increases south (row 0 at the north pole). This is
    what web tile servers use.
  * TMS: same x, but row 0 at the south pole. MBTiles stores rows in TMS.
      tms_row = 2**z - 1 - xyz_y
  * Pixel coordinates are in the "global pixel" frame for a given zoom:
    (0, 0) is the top-left of tile (0, 0); TILE_SIZE pixels per tile.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterator

TILE_SIZE = 256
MAX_LAT = 85.05112878  # Web Mercator latitude limit


def clamp_lat(lat: float) -> float:
    return max(-MAX_LAT, min(MAX_LAT, lat))


def lonlat_to_pixel(lon: float, lat: float, z: int) -> tuple[float, float]:
    """Global pixel coordinates at zoom z (floats, not clipped to a tile)."""
    lat = clamp_lat(lat)
    n = TILE_SIZE * (1 << z)
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def pixel_to_lonlat(px: float, py: float, z: int) -> tuple[float, float]:
    n = TILE_SIZE * (1 << z)
    lon = px / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * py / n))))
    return lon, lat


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    """XYZ tile containing (lon, lat)."""
    px, py = lonlat_to_pixel(lon, lat, z)
    n = 1 << z
    x = min(n - 1, max(0, int(px // TILE_SIZE)))
    y = min(n - 1, max(0, int(py // TILE_SIZE)))
    return x, y


def tile_bounds(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) in degrees of XYZ tile (x, y, z)."""
    west, north = pixel_to_lonlat(x * TILE_SIZE, y * TILE_SIZE, z)
    east, south = pixel_to_lonlat((x + 1) * TILE_SIZE, (y + 1) * TILE_SIZE, z)
    return west, south, east, north


def tile_bounds_3857(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    """Tile bounds in EPSG:3857 metres (xmin, ymin, xmax, ymax)."""
    r = 6378137.0
    origin = math.pi * r
    size = 2 * origin / (1 << z)
    xmin = -origin + x * size
    xmax = xmin + size
    ymax = origin - y * size
    ymin = ymax - size
    return xmin, ymin, xmax, ymax


def xyz_to_tms(y: int, z: int) -> int:
    return (1 << z) - 1 - y


def tms_to_xyz(row: int, z: int) -> int:
    return (1 << z) - 1 - row


@dataclass(frozen=True)
class BBox:
    west: float
    south: float
    east: float
    north: float

    def __post_init__(self):
        if not (-180 <= self.west < self.east <= 180):
            raise ValueError(f"bad longitudes: {self.west}, {self.east}")
        if not (-90 <= self.south < self.north <= 90):
            raise ValueError(f"bad latitudes: {self.south}, {self.north}")

    @classmethod
    def from_dict(cls, d) -> "BBox":
        """BBox from a {west, south, east, north} mapping such as a project file's export_area.

        Raises KeyError, TypeError or ValueError on missing, non-numeric or out-of-range values."""
        return cls(*(float(d[k]) for k in ("west", "south", "east", "north")))

    def tile_range(self, z: int) -> tuple[int, int, int, int]:
        """(xmin, ymin, xmax, ymax) inclusive XYZ tile range covering the bbox."""
        # Work in pixel space with a small inward epsilon so that a bbox edge
        # sitting exactly on a tile boundary (CalTopo snaps its export bounds to
        # tile edges) doesn't pull in an extra empty row/column, and so that
        # float noise in a snapped edge can't push the range outward either.
        eps = 1e-4  # pixels
        n = 1 << z
        px_w, py_n = lonlat_to_pixel(self.west, self.north, z)
        px_e, py_s = lonlat_to_pixel(self.east, self.south, z)
        xmin = int(math.floor((px_w + eps) / TILE_SIZE))
        ymin = int(math.floor((py_n + eps) / TILE_SIZE))
        xmax = int(math.ceil((px_e - eps) / TILE_SIZE)) - 1
        ymax = int(math.ceil((py_s - eps) / TILE_SIZE)) - 1
        xmin, ymin = max(0, xmin), max(0, ymin)
        xmax, ymax = min(n - 1, max(xmin, xmax)), min(n - 1, max(ymin, ymax))
        return xmin, ymin, xmax, ymax

    def tiles(self, z: int) -> Iterator[tuple[int, int, int]]:
        xmin, ymin, xmax, ymax = self.tile_range(z)
        for y in range(ymin, ymax + 1):
            for x in range(xmin, xmax + 1):
                yield x, y, z

    def tile_count(self, z: int) -> int:
        xmin, ymin, xmax, ymax = self.tile_range(z)
        return (xmax - xmin + 1) * (ymax - ymin + 1)

    def total_tiles(self, zmin: int, zmax: int) -> int:
        return sum(self.tile_count(z) for z in range(zmin, zmax + 1))

    def snapped(self, z: int) -> "BBox":
        """Expand to the edges of the tiles at zoom z that cover this bbox."""
        xmin, ymin, xmax, ymax = self.tile_range(z)
        w, _, _, n = tile_bounds(xmin, ymin, z)
        _, s, e, _ = tile_bounds(xmax, ymax, z)
        return BBox(w, s, e, n)

    @property
    def center(self) -> tuple[float, float]:
        return (self.west + self.east) / 2, (self.south + self.north) / 2

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.west, self.south, self.east, self.north

    def contains(self, lon: float, lat: float) -> bool:
        return self.west <= lon <= self.east and self.south <= lat <= self.north

    def intersects(self, other: "BBox") -> bool:
        return not (
            other.west > self.east
            or other.east < self.west
            or other.south > self.north
            or other.north < self.south
        )

    def buffered_deg(self, dlon: float, dlat: float) -> "BBox":
        return BBox(
            max(-180, self.west - dlon),
            max(-90, self.south - dlat),
            min(180, self.east + dlon),
            min(90, self.north + dlat),
        )


def ground_resolution_m(lat: float, z: int) -> float:
    """Metres per pixel at latitude lat and zoom z (256 px tiles)."""
    return 156543.03392 * math.cos(math.radians(lat)) / (1 << z)
