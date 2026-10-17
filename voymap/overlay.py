"""Rasterize vector features (tracks, waypoints, polygons) onto 256 px tiles.

The renderer draws into the *global pixel* frame of the requested zoom and
lets Pillow clip to the tile, so a line or label that crosses a tile edge is
drawn consistently from both sides. Lines are supersampled 2x for antialiasing.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Iterable, Optional

from PIL import Image, ImageDraw, ImageFont

from .features import Feature
from .tilemath import TILE_SIZE, BBox, lonlat_to_pixel, tile_bounds

SS = 2  # supersample factor


def _hex_to_rgba(color: str, opacity: float = 1.0) -> tuple[int, int, int, int]:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        r, g, b = 255, 0, 0
    return r, g, b, max(0, min(255, int(round(255 * opacity))))


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Arial.ttf", "arial.ttf",
                 "LiberationSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class OverlayRenderer:
    def __init__(self, features: Iterable[Feature]):
        self.features = [f for f in features if f.visible and f.coords]
        self._bboxes: dict[str, Optional[BBox]] = {f.id: f.bbox() for f in self.features}

    def is_empty(self) -> bool:
        return not self.features

    def candidates(self, x: int, y: int, z: int) -> list[Feature]:
        """Features whose bbox (buffered by a generous pixel margin) touches the tile."""
        w, s, e, n = tile_bounds(x, y, z)
        # margin: 64 px worth of degrees at this zoom (covers wide lines + labels)
        dlon = (e - w) * 64 / TILE_SIZE
        dlat = (n - s) * 64 / TILE_SIZE
        tb = BBox(w, s, e, n).buffered_deg(dlon, dlat)
        out = []
        for f in self.features:
            if z < f.min_zoom:
                continue
            fb = self._bboxes[f.id]
            if fb and fb.intersects(tb):
                out.append(f)
        return out

    def render_tile(self, x: int, y: int, z: int) -> Optional[Image.Image]:
        """RGBA tile image, or None if nothing to draw here."""
        feats = self.candidates(x, y, z)
        if not feats:
            return None
        ox, oy = x * TILE_SIZE, y * TILE_SIZE
        size = TILE_SIZE * SS
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        def px(p):
            gx, gy = lonlat_to_pixel(p[0], p[1], z)
            return (gx - ox) * SS, (gy - oy) * SS

        # draw order: polygons, lines, then points on top
        for f in feats:
            if f.kind == "polygon":
                st = f.polygon_style()
                for ring in f.coords:
                    pts = [px(p) for p in ring]
                    if len(pts) >= 3:
                        draw.polygon(pts, fill=_hex_to_rgba(st.color, st.fill_opacity))
                        draw.line(pts + [pts[0]], fill=_hex_to_rgba(st.color, st.opacity),
                                  width=max(1, int(round(st.width * SS))), joint="curve")
        for f in feats:
            if f.kind == "line":
                st = f.line_style()
                width = max(1, int(round(st.width * SS)))
                color = _hex_to_rgba(st.color, st.opacity)
                for seg in f.coords:
                    pts = [px(p) for p in seg]
                    if len(pts) < 2:
                        continue
                    if st.dash:
                        _dashed_line(draw, pts, color, width, [d * SS for d in st.dash])
                    else:
                        draw.line(pts, fill=color, width=width, joint="curve")
        labels = []
        for f in feats:
            if f.kind == "point":
                st = f.marker_style()
                cx, cy = px(f.point)
                r = st.size * SS / 2
                draw.ellipse([cx - r - SS, cy - r - SS, cx + r + SS, cy + r + SS], fill=(255, 255, 255, 230))
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_hex_to_rgba(st.color))
                if st.label and f.name:
                    labels.append((cx + r + 3 * SS, cy, f.name, st.label_size))
        for lx, ly, text, lsize in labels:
            font = _font(lsize * SS)
            draw.text((lx, ly), text, font=font, fill=(0, 0, 0, 255), anchor="lm",
                      stroke_width=2 * SS, stroke_fill=(255, 255, 255, 235))

        return img.resize((TILE_SIZE, TILE_SIZE), Image.LANCZOS)


def _dashed_line(draw: ImageDraw.ImageDraw, pts, color, width, pattern):
    """Draw a polyline with an on/off dash pattern (pixel lengths)."""
    import math
    on_len, off_len = (pattern + [pattern[0]])[:2]
    on = True
    remaining = on_len
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg_len = math.hypot(x1 - x0, y1 - y0)
        if seg_len == 0:
            continue
        t = 0.0
        while t < seg_len:
            step = min(remaining, seg_len - t)
            if on:
                ax, ay = x0 + (x1 - x0) * t / seg_len, y0 + (y1 - y0) * t / seg_len
                bx, by = x0 + (x1 - x0) * (t + step) / seg_len, y0 + (y1 - y0) * (t + step) / seg_len
                draw.line([(ax, ay), (bx, by)], fill=color, width=width)
            t += step
            remaining -= step
            if remaining <= 0:
                on = not on
                remaining = on_len if on else off_len
