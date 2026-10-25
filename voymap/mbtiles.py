"""MBTiles writer/reader matched to a CalTopo export that is known to load on a
Trail Tech Voyager Pro (inspected 2026-10-17):

  * tables: ``metadata(name text, value text)``,
            ``tiles(zoom_level integer, tile_column integer, tile_row integer, tile_data blob)``
  * indexes: idx_z, idx_x, idx_y (single-column; no unique index)
  * tile_row in TMS order (row 0 = south)
  * 256x256 PNG, palette mode (<=256 colours) with a tRNS chunk
  * metadata keys, exactly: name, type=baselayer, version=1, description,
    format=png, created=<epoch ms>, bounds=<w,s,e,n>
    (no minzoom/maxzoom/center — add with ``spec_extras=True`` if you want to
    test whether the device tolerates them)
"""
from __future__ import annotations

import io
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image

from .tilemath import BBox, xyz_to_tms

ENCODINGS = ("png8", "png", "jpeg")


@dataclass
class TileEncoder:
    encoding: str = "png8"      # png8 = palette PNG (matches CalTopo), png = truecolor, jpeg
    jpeg_quality: int = 80
    colors: int = 256

    @property
    def format_name(self) -> str:
        return "jpg" if self.encoding == "jpeg" else "png"

    def encode(self, img: Image.Image) -> bytes:
        buf = io.BytesIO()
        if self.encoding == "jpeg":
            img.convert("RGB").save(buf, "JPEG", quality=self.jpeg_quality, optimize=True)
        elif self.encoding == "png":
            img.save(buf, "PNG", optimize=True)
        else:  # png8
            if img.mode == "RGBA" and img.getextrema()[3][0] < 255:
                q = img.quantize(colors=self.colors, method=Image.Quantize.FASTOCTREE)
            else:
                q = img.convert("RGB").quantize(colors=self.colors, method=Image.Quantize.MEDIANCUT)
            q.save(buf, "PNG", optimize=True)
        return buf.getvalue()


class MBTilesWriter:
    def __init__(self, path: str | Path, name: str, bounds: BBox,
                 encoder: Optional[TileEncoder] = None, description: str = "N/A",
                 attribution: str = "", spec_extras: bool = False, overwrite: bool = True):
        self.path = Path(path)
        if not overwrite and self.path.exists():
            raise FileExistsError(self.path)
        # Write to a sibling and rename on success, so a cancelled/failed export
        # never truncates or replaces an existing good file.
        self.tmp_path = self.path.with_name(self.path.name + ".part")
        self.tmp_path.unlink(missing_ok=True)
        self.name = name
        self.bounds = bounds
        self.encoder = encoder or TileEncoder()
        self.description = description
        self.attribution = attribution
        self.spec_extras = spec_extras
        self.conn = sqlite3.connect(self.tmp_path)
        self.conn.execute("PRAGMA journal_mode=OFF")
        self.conn.execute("PRAGMA synchronous=OFF")
        self.conn.execute("CREATE TABLE metadata (name text, value text)")
        self.conn.execute("CREATE TABLE tiles (zoom_level integer, tile_column integer, "
                          "tile_row integer, tile_data blob)")
        self.count = 0
        self.bytes = 0
        self.zmin: Optional[int] = None
        self.zmax: Optional[int] = None
        self._batch: list[tuple[int, int, int, bytes]] = []

    def add_tile_xyz(self, x: int, y: int, z: int, data: bytes) -> None:
        """Add an already-encoded tile addressed in XYZ (web) coordinates."""
        row = xyz_to_tms(y, z)
        self._batch.append((z, x, row, data))
        self.count += 1
        self.bytes += len(data)
        self.zmin = z if self.zmin is None else min(self.zmin, z)
        self.zmax = z if self.zmax is None else max(self.zmax, z)
        if len(self._batch) >= 500:
            self._flush()

    def add_image_xyz(self, x: int, y: int, z: int, img: Image.Image) -> bytes:
        data = self.encoder.encode(img)
        self.add_tile_xyz(x, y, z, data)
        return data

    def _flush(self) -> None:
        if self._batch:
            self.conn.executemany("INSERT INTO tiles VALUES (?,?,?,?)", self._batch)
            self._batch.clear()
            self.conn.commit()

    def abort(self) -> None:
        """Discard the partial file; the target path is left untouched."""
        if self.conn is None:
            return
        self.conn.close()
        self.conn = None
        self.tmp_path.unlink(missing_ok=True)

    def close(self) -> None:
        if self.conn is None:
            return
        self._flush()
        w, s, e, n = self.bounds.as_tuple()
        meta = [
            ("name", self.name),
            ("type", "baselayer"),
            ("version", "1"),
            ("description", self.description or "N/A"),
            ("format", self.encoder.format_name),
            ("created", str(int(time.time() * 1000))),
            ("bounds", f"{w},{s},{e},{n}"),
        ]
        if self.spec_extras:
            cx, cy = self.bounds.center
            meta += [
                ("minzoom", str(self.zmin if self.zmin is not None else 0)),
                ("maxzoom", str(self.zmax if self.zmax is not None else 0)),
                ("center", f"{cx},{cy},{self.zmin if self.zmin is not None else 0}"),
            ]
            if self.attribution:
                meta.append(("attribution", self.attribution))
        self.conn.executemany("INSERT INTO metadata VALUES (?,?)", meta)
        self.conn.execute("CREATE INDEX 'idx_z' on 'tiles' ('zoom_level')")
        self.conn.execute("CREATE INDEX 'idx_x' on 'tiles' ('tile_column')")
        self.conn.execute("CREATE INDEX 'idx_y' on 'tiles' ('tile_row')")
        self.conn.commit()
        self.conn.close()
        self.conn = None
        os.replace(self.tmp_path, self.path)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.close()
        else:
            self.abort()


# ----------------------------------------------------------------------------
# Reading / inspection
# ----------------------------------------------------------------------------

@dataclass
class ZoomStats:
    zoom: int
    tiles: int
    col_min: int
    col_max: int
    row_min: int
    row_max: int
    avg_bytes: float
    max_bytes: int


def inspect(path: str | Path) -> dict:
    conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    info: dict = {"path": str(path), "size_bytes": Path(path).stat().st_size}
    info["schema"] = [(r[0], r[1]) for r in conn.execute(
        "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name")]
    info["metadata"] = dict(conn.execute("SELECT name, value FROM metadata"))
    zooms = []
    for r in conn.execute(
        "SELECT zoom_level, COUNT(*), MIN(tile_column), MAX(tile_column), MIN(tile_row), "
        "MAX(tile_row), AVG(LENGTH(tile_data)), MAX(LENGTH(tile_data)) FROM tiles "
        "GROUP BY zoom_level ORDER BY zoom_level"):
        zooms.append(ZoomStats(*r))
    info["zooms"] = zooms
    info["tile_count"] = sum(z.tiles for z in zooms)
    row = conn.execute("SELECT tile_data FROM tiles ORDER BY zoom_level DESC LIMIT 1").fetchone()
    if row:
        im = Image.open(io.BytesIO(row[0]))
        info["sample_tile"] = {"format": im.format, "size": im.size, "mode": im.mode}
    conn.close()
    return info


def format_inspection(info: dict) -> str:
    lines = [f"{info['path']}  ({info['size_bytes'] / 1e6:.1f} MB, {info['tile_count']} tiles)"]
    lines.append("schema: " + ", ".join(f"{t}:{n}" for t, n in info["schema"]))
    for k, v in info["metadata"].items():
        lines.append(f"  {k:12s} {v}")
    if "sample_tile" in info:
        st = info["sample_tile"]
        lines.append(f"  tile sample  {st['format']} {st['size'][0]}x{st['size'][1]} mode={st['mode']}")
    for z in info["zooms"]:
        lines.append(f"  z{z.zoom:<3d} {z.tiles:7d} tiles  col {z.col_min}..{z.col_max}  "
                     f"row {z.row_min}..{z.row_max}  avg {z.avg_bytes / 1024:.1f} KB  max {z.max_bytes / 1024:.1f} KB")
    return "\n".join(lines)


def read_tile_xyz(path: str | Path, x: int, y: int, z: int) -> Optional[bytes]:
    conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    row = conn.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                       (z, x, xyz_to_tms(y, z))).fetchone()
    conn.close()
    return row[0] if row else None
