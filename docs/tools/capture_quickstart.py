"""Capture quick-start screenshots of the desktop app offscreen, with real map
imagery served locally from the user's CalTopo sample, and record widget
rectangles for annotation. Usage: capture.py OUTDIR TILEPORT"""
import json, os, sys, tempfile
from pathlib import Path

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
TILEPORT = int(sys.argv[2])

# isolated config/cache with an override of the built-in OpenTopoMap source -> local tiles
cfg = Path(tempfile.mkdtemp()); (cfg / "voymap").mkdir()
os.environ["XDG_CONFIG_HOME"] = str(cfg)
os.environ["XDG_CACHE_HOME"] = str(Path(tempfile.mkdtemp()))
(cfg / "voymap" / "sources.json").write_text(json.dumps([{
    "id": "opentopomap", "name": "OpenTopoMap", "kind": "xyz",
    "url": f"http://127.0.0.1:{TILEPORT}/{{z}}/{{x}}/{{y}}.png", "attribution": "© OpenTopoMap (CC-BY-SA)",
    "min_zoom": 0, "max_zoom": 13, "opaque": True, "bulk_ok": True, "rate_limit": 50, "max_workers": 8}]))

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QTimer, QPoint, QSettings
app = QApplication(sys.argv)
QSettings("voymap", "voymap").clear()
from voymap.gui.main_window import MainWindow
QMessageBox.information = staticmethod(lambda *a, **k: None)   # no modal dialogs offscreen
w = MainWindow(); w.resize(1400, 1000); w.show()
from PySide6.QtCore import Qt
w.resizeDocks([w.layers_dock, w.features_dock, w.export_dock], [330, 120, 520], Qt.Vertical)
W, H = 1400, 1000
rects = {}

def rect(name, widget):
    p = widget.mapTo(w, QPoint(0, 0))
    rects[name] = [p.x(), p.y(), widget.width(), widget.height()]

def row(src_id):
    return next(r for r in w.layer_rows if r.src.id == src_id)

def shot(name):
    w.grab().save(str(OUT / f"{name}.png"))
    print("shot", name, flush=True)

def step1():
    # 1. select the map: OpenTopoMap on, USGS off; opacity slider
    row("usgs_topo").check.setChecked(False)
    row("opentopomap").check.setChecked(True)
    w._js("window.vm.setView(35.19, -111.62, 12)")
    QTimer.singleShot(4000, step1b)

def step1b():
    w._js("document.getElementById('err').style.display='none'; window.addEventListener('error',()=>{}); setInterval(()=>{document.getElementById('err').style.display='none'},200)")
    row("opentopomap").check.setText("OpenTopoMap")
    rect("layer_check", row("opentopomap").check); rect("layer_slider", row("opentopomap").slider)
    rect("mvum_check", row("usfs_mvum_labeled").check)
    shot("01_layers")
    # show opacity effect
    row("opentopomap").slider.setValue(60)
    QTimer.singleShot(1500, step2)

def step2():
    shot("02_opacity")
    row("opentopomap").slider.setValue(100)
    # 2. export area: button + the rectangle on the map
    rect("draw_btn", w.draw_area_btn)
    w._js("window.vm.setExportArea(-111.72, 35.14, -111.52, 35.26)")
    QTimer.singleShot(2000, step3)

def step3():
    rect("area_label", w.area_label); rect("estimate", w.estimate_label); rect("zoom_table", w.zoom_table)
    rect("zmin", w.e_zmin); rect("zmax", w.e_zmax); rect("name", w.e_name); rect("theme", w.e_theme)
    rect("export_btn", w.export_btn)
    shot("03_area")
    # 3. export: run it offline against the local tiles, zoom 8-13 (sample has z<=13)
    w.e_zmin.setValue(8); w.e_zmax.setValue(13); w.e_name.setText("Flagstaff Ride")
    from voymap.gui.main_window import ExportWorker
    from voymap.export import ExportSpec, LayerSpec
    from voymap.tilemath import BBox
    spec = ExportSpec(output=Path(tempfile.mkdtemp()) / "Flagstaff Ride.mbtiles", name="Flagstaff Ride",
                      bbox=BBox(-111.72, 35.14, -111.52, 35.26), zmin=8, zmax=13, layers=[LayerSpec("opentopomap")], snap_zoom=11)
    w.worker = ExportWorker([spec], w.sources); w._file_index = 0
    w.worker.progress.connect(w._on_progress); w.worker.finished_ok.connect(w._on_export_done)
    w.export_btn.setEnabled(False); w.cancel_btn.setEnabled(True)
    w.worker.start()
    QTimer.singleShot(1200, step3b)

def step3b():
    rect("overall_bar", w.overall_progress); rect("step_bar", w.progress); rect("cancel_btn", w.cancel_btn)
    shot("04_export_running")
    def done():
        if w.worker.isRunning():
            QTimer.singleShot(500, done); return
        QTimer.singleShot(800, step4)
    done()

def step4():
    rect("progress_label", w.progress_label)
    shot("05_export_done")
    # 4. map region mapping: where the map canvas is, for the drag arrow
    rect("map", w.view)
    rect("menu", w.menuBar())
    (OUT / "rects.json").write_text(json.dumps(rects, indent=1))
    w.view.page().runJavaScript("(function(){const r=document.querySelector('.export-area').getBoundingClientRect();return [r.left,r.top,r.right,r.bottom];})()",
                                lambda v: ((OUT / "area_px.json").write_text(json.dumps(v)), app.quit()))

QTimer.singleShot(6000, step1)
QTimer.singleShot(120000, app.quit)
app.exec()
