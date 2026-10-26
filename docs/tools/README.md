# Quick start screenshot tooling

These scripts regenerate the annotated screenshots in `docs/img/` from the
real app, offscreen, with map imagery served from a local MBTiles file (so
no network is needed and the imagery is stable between runs).

```bash
# 1. serve tiles from an MBTiles file (any export that covers the area)
python docs/tools/mbtiles_tileserver.py path/to/sample.mbtiles 8877 &

# 2. run the app offscreen and capture the raw screenshots + widget rectangles
QT_QPA_PLATFORM=offscreen python docs/tools/capture_quickstart.py /tmp/shots 8877

# 3. draw the numbered callouts
python docs/tools/annotate_quickstart.py /tmp/shots docs/img

# 4. rebuild the PDF
cd docs && pdflatex quickstart.tex && pdflatex quickstart.tex
```

`capture_quickstart.py` overrides the built-in OpenTopoMap source with the
local tile server through a temporary `sources.json`, so the Layers panel
shows the real source name. It sets a 1400x1000 window and resizes the docks
so the Export panel is fully visible. `annotate_quickstart.py` reads the
widget rectangles the capture step wrote (`rects.json`) and finds the drawn
export rectangle by its colour, so callouts land on the right pixels even if
the layout shifts.

Requires Pillow, numpy, PySide6 and a TeX installation for the PDF.
