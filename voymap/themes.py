"""Tile colour themes applied after the base/overlay layers are composited and
before tracks and waypoints are drawn (so feature colours stay as chosen).

  day    unchanged
  night  invert luminance, keep hues: invert RGB, then rotate hue by 180°.
         White paper becomes near-black; blue water stays blue; brown
         contours stay brown. Then dim slightly. This is the same transform the
         GUI previews with the CSS filter ``invert(1) hue-rotate(180deg)``.
  dim    keep colours, multiply brightness (low-glare "dusk" map)
"""
from __future__ import annotations

from PIL import Image, ImageEnhance, ImageOps

THEMES = ("day", "night", "dim")


def apply_theme(img: Image.Image, theme: str, brightness: float = 0.9) -> Image.Image:
    if theme == "day":
        return img
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    rgb = img.convert("RGB")
    alpha = img.getchannel("A")
    if theme == "night":
        rgb = ImageOps.invert(rgb)
        h, s, v = rgb.convert("HSV").split()
        h = h.point(lambda x: (x + 128) % 256)
        rgb = Image.merge("HSV", (h, s, v)).convert("RGB")
        if brightness != 1.0:
            rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    elif theme == "dim":
        rgb = ImageEnhance.Brightness(rgb).enhance(min(brightness, 0.7))
    else:
        raise ValueError(f"unknown theme {theme!r}")
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def css_filter(theme: str) -> str:
    """CSS filter string the Leaflet preview uses for the same look."""
    return {"day": "none", "night": "invert(1) hue-rotate(180deg) brightness(0.9)",
            "dim": "brightness(0.7)"}[theme]
