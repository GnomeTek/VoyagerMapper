"""Draw numbered callouts on the captured screenshots. Usage: annotate.py SHOTS OUTDIR"""
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

SH = Path(sys.argv[1]); OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
R = json.load(open(SH / "rects.json"))
RED = (220, 30, 30)
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
SMALL = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)


def box(d, rect, pad=5, w=3):
    x, y, bw, bh = rect
    d.rounded_rectangle([x - pad, y - pad, x + bw + pad, y + bh + pad], radius=6, outline=RED, width=w)


def badge(d, xy, n):
    x, y = xy; r = 16
    d.ellipse([x - r, y - r, x + r, y + r], fill=RED, outline="white", width=2)
    d.text((x, y), str(n), fill="white", font=FONT, anchor="mm")


def label(d, xy, text):
    x, y = xy
    tw = d.textlength(text, font=SMALL)
    d.rounded_rectangle([x - 6, y - 12, x + tw + 6, y + 12], radius=5, fill=(255, 255, 255), outline=RED, width=2)
    d.text((x, y), text, fill=RED, font=SMALL, anchor="lm")


def arrow(d, a, b, w=4):
    import math
    d.line([a, b], fill=RED, width=w)
    ang = math.atan2(b[1] - a[1], b[0] - a[0]); L = 18
    for s in (+0.5, -0.5):
        d.line([b, (b[0] - L * math.cos(ang - s), b[1] - L * math.sin(ang - s))], fill=RED, width=w)


def crop_right(img, top=0, bottom=None):
    return img.crop((900, top, img.width, bottom or img.height))


# ---- 1. choose the map layer + opacity
im = Image.open(SH / "01_layers.png").convert("RGB"); d = ImageDraw.Draw(im)
box(d, R["layer_check"]); badge(d, (R["layer_check"][0] - 24, R["layer_check"][1] + 10), 1)
box(d, R["layer_slider"], pad=7); badge(d, (R["layer_slider"][0] + R["layer_slider"][2] + 2, R["layer_slider"][1] + 7), 2)
crop_right(im, 0, 300).save(OUT / "qs1_layers.png")

# ---- 2. opacity effect, full window (map faded)
im = Image.open(SH / "02_opacity.png").convert("RGB"); d = ImageDraw.Draw(im)
box(d, R["layer_slider"], pad=7); badge(d, (R["layer_slider"][0] + R["layer_slider"][2] + 2, R["layer_slider"][1] + 7), 2)
label(d, (R["map"][0] + 24, R["map"][1] + 40), "The map at 60 % opacity")
im.save(OUT / "qs2_opacity.png")

# ---- 3. draw the export area
im = Image.open(SH / "03_area.png").convert("RGB"); d = ImageDraw.Draw(im)
box(d, R["draw_btn"]); badge(d, (R["draw_btn"][0] - 22, R["draw_btn"][1] + 11), 3)
# the rectangle on the map (magenta dashed) — pixel bounds measured from the rendered page
# locate the dashed magenta outline (#d400ff) inside the map area
import numpy as np
arr = np.array(Image.open(SH / "03_area.png").convert("RGB")).astype(int)
mx, my, mw, mh = R["map"]
sub = arr[my:my + mh, mx:mx + mw]
mask = (abs(sub[..., 0] - 212) < 25) & (sub[..., 1] < 40) & (abs(sub[..., 2] - 255) < 25)
# the dashed outline is the only feature with >= 200 magenta pixels on one row/column
rows = np.where(mask.sum(axis=1) >= 200)[0]; cols = np.where(mask.sum(axis=0) >= 200)[0]
ay0, ay1 = my + rows.min(), my + rows.max(); ax0, ax1 = mx + cols.min(), mx + cols.max()
arrow(d, (ax0 + 10, ay0 + 10), (ax1 - 14, ay1 - 14))
badge(d, (ax0 + 10, ay0 + 10), 4); badge(d, (ax1 - 14, ay1 - 14), 5)
label(d, (ax0 + 40, ay0 - 28), "4  Press at one corner...")
label(d, (ax1 - 250, ay1 + 30), "5  ...drag, and release at the opposite corner")
box(d, R["estimate"], pad=4); badge(d, (R["estimate"][0] - 22, R["estimate"][1] + 15), 6)
im.save(OUT / "qs3_area.png")

# ---- 4. export settings + button (right panel crop of the done shot, before running)
im = Image.open(SH / "03_area.png").convert("RGB"); d = ImageDraw.Draw(im)
box(d, R["name"], pad=4); badge(d, (R["name"][0] - 22, R["name"][1] + 11), 7)
box(d, R["zmin"], pad=4); box(d, R["zmax"], pad=4); badge(d, (R["zmax"][0] + R["zmax"][2] + 2, R["zmax"][1] + 12), 8)
box(d, R["export_btn"]); badge(d, (R["export_btn"][0] - 22, R["export_btn"][1] + 11), 9)
top = R["name"][1] - 40
crop_right(im, top, R["export_btn"][1] + 40).save(OUT / "qs4_export.png")

# ---- 5. progress
im = Image.open(SH / "04_export_running.png").convert("RGB"); d = ImageDraw.Draw(im)
box(d, R["overall_bar"], pad=4); box(d, R["step_bar"], pad=4)
badge(d, (R["overall_bar"][0] - 22, R["overall_bar"][1] + 11), 10)
box(d, R["cancel_btn"], pad=4)
crop_right(im, R["export_btn"][1] - 12, R["progress_label"][1] + 40).save(OUT / "qs5_progress.png")
print("annotated", sorted(p.name for p in OUT.glob("*.png")))
