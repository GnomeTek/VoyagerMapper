"""Main window: Leaflet map (QWebEngineView) + Layers / Features / Export docks."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QColor, QDesktopServices, QKeySequence
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDockWidget,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
                               QToolButton, QVBoxLayout, QWidget)

from .. import __version__
from ..export import AVG_TILE_BYTES, ExportJob, ExportSpec, LayerSpec, Progress
from ..features import (Feature, features_bbox, features_from_geojson, features_to_geojson,
                        parse_gpx, write_gpx)
from ..mbtiles import format_inspection, inspect
from ..sources import load_sources
from ..themes import THEMES, css_filter
from ..tilemath import BBox
from .bridge import Bridge
from .server import LocalServer
from ..fetch import TileFetcher, default_cache_dir
from ..sources import load_user_sources, save_user_sources, user_sources_path, write_user_sources_template
from .source_dialog import SourceDialog

WEB_DIR = Path(__file__).parent / "web"


def _read_qrc(path: str) -> bytes:
    """Read a file out of Qt's compiled-in resources (e.g. qwebchannel.js)."""
    from PySide6.QtCore import QFile, QIODevice
    f = QFile(path)
    if not f.open(QIODevice.ReadOnly):
        raise RuntimeError(f"cannot read Qt resource {path}")
    try:
        return bytes(f.readAll().data())
    finally:
        f.close()


class MapPage(QWebEnginePage):
    """Keeps the map view on the loopback page: the web channel stays with our own origin.

    Web link clicks elsewhere (e.g. the Leaflet attribution) open in the system browser; any
    other navigation away, and every popup window, is refused. Qt reports script-set
    ``location`` as a link click too, so only http(s) is ever handed to the OS.
    """

    def __init__(self, port: int, parent=None):
        super().__init__(parent)
        self.port = port

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() == "http" and url.host() == "127.0.0.1" and url.port() == self.port:
            return True
        if nav_type == QWebEnginePage.NavigationTypeLinkClicked and url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
        return False

    def createWindow(self, _type):
        return None


# ----------------------------------------------------------------------------
# Export worker
# ----------------------------------------------------------------------------
class ExportWorker(QThread):
    """Runs one or more ExportSpecs in sequence (e.g. a day + night pair)."""
    progress = Signal(object)
    finished_ok = Signal(object)   # list[(ExportSpec, Progress)]
    failed = Signal(str)

    def __init__(self, specs: list[ExportSpec], sources):
        super().__init__()
        self.specs = specs
        self.sources = sources
        self.job: Optional[ExportJob] = None
        self._cancelled = False

    def run(self):
        results = []
        try:
            for spec in self.specs:
                if self._cancelled:
                    break
                self.job = ExportJob(spec, sources=self.sources, progress=lambda p: self.progress.emit(p))
                results.append((spec, self.job.run()))
            self.finished_ok.emit(results)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))

    def cancel(self):
        self._cancelled = True
        if self.job:
            self.job.cancel()


# ----------------------------------------------------------------------------
# Layer row widget
# ----------------------------------------------------------------------------
class LayerRow(QWidget):
    changed = Signal()

    def __init__(self, src):
        super().__init__()
        self.src = src
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        label = src.name if (src.bulk_ok or "preview" in src.name) else src.name + "  [preview only]"
        self.check = QCheckBox(("★ " if src.user else "") + label)
        self.check.setToolTip(src.notes or src.url)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(100)
        self.slider.setFixedWidth(90)
        self.slider.setToolTip("opacity")
        lay.addWidget(self.check, 1)
        lay.addWidget(self.slider)
        self.check.toggled.connect(self.changed)
        self.slider.valueChanged.connect(self.changed)

    @property
    def visible(self) -> bool:
        return self.check.isChecked()

    @property
    def opacity(self) -> float:
        return self.slider.value() / 100


# ----------------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"voymap {__version__} — Voyager Pro map builder")
        self.resize(1400, 900)
        self.settings = QSettings("voymap", "voymap")
        self.sources = load_sources()
        self.features: list[Feature] = []
        self.export_area: Optional[dict] = None
        self.worker: Optional[ExportWorker] = None
        self.project_path: Optional[Path] = None
        self._page_ready = False

        # -- map view
        self.server = LocalServer(WEB_DIR, extra={"/qwebchannel.js": (_read_qrc(":/qtwebchannel/qwebchannel.js"),
                                                                     "application/javascript")})
        self.view = QWebEngineView()
        self.view.setPage(MapPage(self.server.port, self.view))
        self.bridge = Bridge()
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self.setCentralWidget(self.view)
        self.bridge.sig_ready.connect(self._on_page_ready)
        self.bridge.sig_features.connect(self._on_features_from_js)
        self.bridge.sig_export_area.connect(self._on_export_area)
        self.bridge.sig_selected.connect(self._on_feature_selected)
        self.bridge.sig_view.connect(self._on_view_changed)
        self.bridge.sig_log.connect(lambda m: self.statusBar().showMessage(m, 5000))

        self._build_layers_dock()
        self._build_features_dock()
        self._build_export_dock()
        self._build_menu()
        self.statusBar().showMessage("loading map page…")
        self.view.load(QUrl(self.server.url("map.html")))

    # ------------------------------------------------------------------ docks
    def _make_dock(self, title: str, content: QWidget) -> QDockWidget:
        """Dock whose content scrolls instead of overlapping when the window is short."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        scroll.setMinimumWidth(content.sizeHint().width() + 24)
        dock = QDockWidget(title, self)
        dock.setWidget(scroll)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        return dock

    def _build_layers_dock(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.layer_rows: list[LayerRow] = []
        self.rows_box = QWidget()
        self.rows_lay = QVBoxLayout(self.rows_box)
        self.rows_lay.setContentsMargins(0, 0, 0, 0)
        self.rows_lay.setSpacing(2)
        lay.addWidget(self.rows_box)
        self._rebuild_layer_rows()
        mrow = QHBoxLayout()
        b = QPushButton("Add source…"); b.clicked.connect(self._add_source); mrow.addWidget(b)
        b = QPushButton("Edit…"); b.setToolTip("Edit the selected user-added source"); b.clicked.connect(self._edit_source); mrow.addWidget(b)
        b = QPushButton("Remove"); b.setToolTip("Remove the selected user-added source"); b.clicked.connect(self._remove_source); mrow.addWidget(b)
        lay.addLayout(mrow)
        hint = QLabel("Opaque sources are base maps; check one base plus any overlays. Hover a name for notes. "
                      "Select a row by clicking its name; your own sources are marked ★ and can be edited or removed.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 11px")
        hint.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        lay.addWidget(hint)
        srow = QHBoxLayout()
        b = QPushButton("Source details…")
        b.setToolTip("URLs, zoom ranges, usage notes, and how to add your own sources")
        b.clicked.connect(self._show_sources_dialog)
        srow.addWidget(b)
        lay.addLayout(srow)
        test_btn = QPushButton("Test sources (fetch one tile each)")
        test_btn.setToolTip("Downloads one tile at the current map centre for every checked layer through "
                            "the Python fetcher and reports the HTTP result. Separates a page problem from a network one.")
        test_btn.clicked.connect(self._test_sources)
        lay.addWidget(test_btn)
        self.grid_check = QCheckBox("Show tile grid (z/x/y)")
        self.grid_check.toggled.connect(lambda on: self._js(f"window.vm.showTilesGrid({'true' if on else 'false'})"))
        lay.addWidget(self.grid_check)
        self.layers_dock = self._make_dock("Layers (bottom → top)", w)

    def _build_features_dock(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        btns = QHBoxLayout()
        for label, kind in (("Draw track", "Line"), ("Add waypoint", "Marker"), ("Draw area", "Polygon")):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, k=kind: self._js(f"window.vm.startDraw('{k}')"))
            btns.addWidget(b)
        lay.addLayout(btns)
        self.feature_list = QListWidget()
        self.feature_list.setMinimumHeight(90)
        self.feature_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.feature_list.currentItemChanged.connect(self._on_feature_list_select)
        lay.addWidget(self.feature_list, 1)

        self.style_box = QGroupBox("Selected feature")
        form = QFormLayout(self.style_box)
        self.f_name = QLineEdit()
        self.f_name.editingFinished.connect(self._apply_feature_style)
        self.f_color = QPushButton("color")
        self.f_color.clicked.connect(self._pick_color)
        self.f_width = QDoubleSpinBox()
        self.f_width.setRange(0.5, 20)
        self.f_width.setValue(3)
        self.f_width.valueChanged.connect(self._apply_feature_style)
        self.f_label = QCheckBox("draw name label on map tiles")
        self.f_label.setChecked(True)
        self.f_label.toggled.connect(self._apply_feature_style)
        self.f_visible = QCheckBox("include in export")
        self.f_visible.setChecked(True)
        self.f_visible.toggled.connect(self._apply_feature_style)
        self.f_minzoom = QSpinBox()
        self.f_minzoom.setRange(0, 18)
        self.f_minzoom.setToolTip("Don't draw this feature on tiles below this zoom (0 = always)")
        self.f_minzoom.valueChanged.connect(self._apply_feature_style)
        form.addRow("Name", self.f_name)
        form.addRow("Color", self.f_color)
        form.addRow("Width / size (px)", self.f_width)
        form.addRow("", self.f_label)
        form.addRow("", self.f_visible)
        form.addRow("Min zoom", self.f_minzoom)
        row = QHBoxLayout()
        b = QPushButton("Extend track")
        b.setToolTip("Continue adding points from the end of the selected track")
        b.clicked.connect(lambda: self._js(f"window.vm.extendTrack({json.dumps(self._selected_id())})"))
        row.addWidget(b)
        b = QPushButton("Zoom to")
        b.clicked.connect(lambda: self._js(f"window.vm.zoomToFeature({json.dumps(self._selected_id())})"))
        row.addWidget(b)
        b = QPushButton("Delete")
        b.clicked.connect(self._delete_selected)
        row.addWidget(b)
        form.addRow(row)
        self.style_box.setEnabled(False)
        lay.addWidget(self.style_box)
        self.features_dock = self._make_dock("Tracks & waypoints", w)

    def _build_export_dock(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.e_name = QLineEdit("My Ride Area")
        self.e_name.setToolTip("Stored in MBTiles metadata 'name' — what the device lists")
        form.addRow("Map name", self.e_name)
        self.e_zmin = QSpinBox(); self.e_zmin.setRange(0, 18); self.e_zmin.setValue(8)
        self.e_zmax = QSpinBox(); self.e_zmax.setRange(0, 18); self.e_zmax.setValue(14)
        zrow = QHBoxLayout(); zrow.addWidget(self.e_zmin); zrow.addWidget(QLabel("to")); zrow.addWidget(self.e_zmax)
        form.addRow("Zoom levels", zrow)
        self.e_zmin.valueChanged.connect(self._update_estimate)
        self.e_zmax.valueChanged.connect(self._update_estimate)
        self.e_snap = QSpinBox(); self.e_snap.setRange(0, 18); self.e_snap.setValue(11)
        self.e_snap.setToolTip("The area is expanded outward to the tile grid of this zoom, so every zoom "
                               "from here up covers exactly the same rectangle (CalTopo snaps only at its max zoom). "
                               "Coarser zooms still take whole tiles, so they overshoot a little. "
                               "A lower value gives a bigger, more uniform area; higher = tighter.")
        self.e_snap.valueChanged.connect(self._update_estimate)
        self.e_snap_on = QCheckBox("snap area to tile grid at zoom")
        self.e_snap_on.setChecked(True)
        self.e_snap_on.toggled.connect(self._update_estimate)
        srow = QHBoxLayout(); srow.addWidget(self.e_snap_on); srow.addWidget(self.e_snap); srow.addStretch()
        form.addRow("", srow)
        self.e_encoding = QComboBox()
        self.e_encoding.addItems(["png8 (palette PNG — matches CalTopo)", "png (truecolor)", "jpeg (smaller; untested on device)"])
        form.addRow("Tile encoding", self.e_encoding)
        self.e_theme = QComboBox()
        self.e_theme.addItems(["Day (as shown)", "Night (inverted luminance, hues kept)", "Dim (70 % brightness)"])
        self.e_theme.setToolTip("Also previews on the map. Tracks and waypoints keep their colours in every theme.")
        self.e_theme.currentIndexChanged.connect(self._apply_theme_preview)
        form.addRow("Theme", self.e_theme)
        self.e_both = QCheckBox("export a day + night pair (<name>_day / <name>_night)")
        form.addRow("", self.e_both)
        lay.addLayout(form)

        # -- Advanced (collapsed by default)
        self.adv_btn = QToolButton()
        self.adv_btn.setText("Advanced options")
        self.adv_btn.setCheckable(True)
        self.adv_btn.setArrowType(Qt.RightArrow)
        self.adv_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.adv_btn.setStyleSheet("QToolButton { border: none; font-weight: bold }")
        lay.addWidget(self.adv_btn)
        self.adv_box = QWidget()
        adv = QFormLayout(self.adv_box)
        adv.setContentsMargins(12, 0, 0, 0)
        self.e_jpegq = QSpinBox(); self.e_jpegq.setRange(30, 100); self.e_jpegq.setValue(80)
        self.e_jpegq.setToolTip("Only used with the jpeg encoding")
        adv.addRow("JPEG quality", self.e_jpegq)
        self.e_colors = QSpinBox(); self.e_colors.setRange(16, 256); self.e_colors.setValue(256)
        self.e_colors.setToolTip("Palette size for png8 (CalTopo uses 256; fewer = smaller files, more banding)")
        adv.addRow("png8 colours", self.e_colors)
        self.e_trim = QCheckBox("trim coarse zooms to the area (transparent; untested on device)")
        self.e_trim.setToolTip("Zooms below the snap zoom take whole tiles that overshoot the area. This paints the "
                               "overshoot transparent so every zoom shows the same extent. CalTopo never writes "
                               "partially transparent tiles, so test it on a small export first.")
        adv.addRow("", self.e_trim)
        self.e_write_empty = QCheckBox("write grey tiles where no layer has data")
        adv.addRow("", self.e_write_empty)
        self.e_extras = QCheckBox("write minzoom/maxzoom/center metadata (untested)")
        adv.addRow("", self.e_extras)
        self.e_refresh = QCheckBox("ignore tile cache (re-download everything)")
        adv.addRow("", self.e_refresh)
        self.e_pad = QDoubleSpinBox(); self.e_pad.setRange(0, 1); self.e_pad.setDecimals(3)
        self.e_pad.setSingleStep(0.005); self.e_pad.setValue(0.01); self.e_pad.setSuffix(" °")
        self.e_pad.setToolTip("Padding around the tracks when using 'Area = tracks extent' (0.01° ≈ 1 km)")
        adv.addRow("Extent padding", self.e_pad)
        self.e_desc = QLineEdit("N/A")
        self.e_desc.setToolTip("MBTiles metadata 'description' (CalTopo writes N/A)")
        adv.addRow("Description", self.e_desc)
        crow = QHBoxLayout()
        self.cache_label = QLabel()
        self.cache_label.setWordWrap(True)
        b = QPushButton("Open cache folder"); b.clicked.connect(self._open_cache); crow.addWidget(b)
        b = QPushButton("Clear cache…"); b.clicked.connect(self._clear_cache); crow.addWidget(b)
        adv.addRow("Tile cache", self.cache_label)
        adv.addRow("", crow)
        self.adv_box.setVisible(False)
        self.adv_btn.toggled.connect(lambda on: (self.adv_box.setVisible(on),
                                                 self.adv_btn.setArrowType(Qt.DownArrow if on else Qt.RightArrow)))
        lay.addWidget(self.adv_box)
        self._refresh_cache_label()

        arow = QHBoxLayout()
        b = QPushButton("Draw export rectangle")
        b.clicked.connect(lambda: self._js("window.vm.startExportRect()"))
        arow.addWidget(b)
        b = QPushButton("Area = tracks extent")
        b.clicked.connect(self._area_from_features)
        arow.addWidget(b)
        b = QPushButton("Clear")
        b.clicked.connect(lambda: self._js("window.vm.clearExportArea()"))
        arow.addWidget(b)
        lay.addLayout(arow)
        self.area_label = QLabel("No export area set.")
        self.area_label.setWordWrap(True)
        lay.addWidget(self.area_label)
        self.estimate_label = QLabel("")
        self.estimate_label.setStyleSheet("font-weight: bold")
        self.estimate_label.setWordWrap(True)
        lay.addWidget(self.estimate_label)
        self.zoom_table = QLabel("")
        self.zoom_table.setStyleSheet("font-family: monospace; color: #444; font-size: 11px")
        lay.addWidget(self.zoom_table)

        self.export_btn = QPushButton("Export MBTiles…")
        self.export_btn.clicked.connect(self._start_export)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_export)
        brow = QHBoxLayout(); brow.addWidget(self.export_btn); brow.addWidget(self.cancel_btn)
        lay.addLayout(brow)
        self.overall_label = QLabel("")
        self.overall_label.setStyleSheet("font-weight: bold")
        lay.addWidget(self.overall_label)
        self.overall_progress = QProgressBar()
        self.overall_progress.setFormat("%p%  overall")
        lay.addWidget(self.overall_progress)
        self.progress = QProgressBar()
        self.progress.setFormat("%p%  this step")
        lay.addWidget(self.progress)
        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)
        lay.addWidget(self.progress_label)
        gpx_btn = QPushButton("Export GPX for Voyager…")
        gpx_btn.clicked.connect(self._export_gpx)
        lay.addWidget(gpx_btn)
        lay.addStretch()
        self.export_dock = self._make_dock("Export to Voyager Pro", w)
        # initial vertical split of the right column; the features list absorbs resizes
        layers_h = self.layers_dock.widget().widget().sizeHint().height() + 30
        export_h = w.sizeHint().height() + 30
        self.resizeDocks([self.layers_dock, self.features_dock, self.export_dock],
                         [layers_h, 260, export_h], Qt.Vertical)

    def _build_menu(self):
        m = self.menuBar().addMenu("&File")
        for text, fn, key in (("Import GPX…", self._import_gpx, "Ctrl+I"),
                              ("Open project…", self._open_project, "Ctrl+O"),
                              ("Save project", self._save_project, "Ctrl+S"),
                              ("Save project as…", self._save_project_as, "Ctrl+Shift+S"),
                              ("Inspect MBTiles…", self._inspect_mbtiles, None),
                              ("Tile sources…", self._show_sources_dialog, None),
                              ("Quit", self.close, "Ctrl+Q")):
            a = QAction(text, self)
            if key:
                a.setShortcut(QKeySequence(key))
            a.triggered.connect(fn)
            m.addAction(a)

    # ------------------------------------------------------------------ JS plumbing
    def _js(self, code: str):
        if self._page_ready:
            self.view.page().runJavaScript(code)

    @Slot()
    def _on_page_ready(self):
        self._page_ready = True
        specs = [s.leaflet_spec() for s in self.sources.values() if s.bulk_ok]
        self._js(f"window.vm.setSources({json.dumps(specs)})")
        self._push_layers()
        v = self.settings.value("view")
        if v:
            lat, lon, z = json.loads(v)
            self._js(f"window.vm.setView({lat},{lon},{int(z)})")
        self.statusBar().showMessage("ready", 3000)

    def _push_layers(self):
        order = [{"id": r.src.id, "opacity": r.opacity, "visible": r.visible} for r in self.layer_rows]
        self._js(f"window.vm.setLayers({json.dumps(order)})")
        self._update_estimate()

    @Slot(str)
    def _on_features_from_js(self, gj: str):
        self.features = features_from_geojson(json.loads(gj))
        self._refresh_feature_list()
        self._update_estimate()

    @Slot(str)
    def _on_export_area(self, js: str):
        self.export_area = None if js == "null" else json.loads(js)
        if self.export_area:
            a = self.export_area
            self.area_label.setText(f"W {a['west']:.5f}  S {a['south']:.5f}  E {a['east']:.5f}  N {a['north']:.5f}"
                                    + ("  (polygon clip)" if a.get("ring") else ""))
        else:
            self.area_label.setText("No export area set.")
        self._update_estimate()

    @Slot(float, float, int)
    def _on_view_changed(self, lat, lon, z):
        self.settings.setValue("view", json.dumps([lat, lon, z]))

    # ------------------------------------------------------------------ layer rows / user sources
    def _rebuild_layer_rows(self, keep_state: Optional[dict] = None):
        state = keep_state or {r.src.id: (r.visible, r.slider.value()) for r in self.layer_rows}
        for r in self.layer_rows:
            self.rows_lay.removeWidget(r)
            r.deleteLater()
        self.layer_rows = []
        # base layers first, overlays after; preview-only sources are not offered at all
        ordered = sorted((s for s in self.sources.values() if s.bulk_ok), key=lambda s: (not s.opaque, s.user, s.id))
        for src in ordered:
            row = LayerRow(src)
            vis, op = state.get(src.id, (src.id == "usgs_topo" and not state, 100))
            row.check.setChecked(bool(vis))
            row.slider.setValue(op)
            row.changed.connect(self._push_layers)
            row.check.clicked.connect(lambda _=False, r=row: self._select_row(r))
            self.layer_rows.append(row)
            self.rows_lay.addWidget(row)
        self._selected_row: Optional[LayerRow] = None

    def _select_row(self, row):
        self._selected_row = row
        for r in self.layer_rows:
            r.setStyleSheet("background: #dbe9ff; border-radius: 3px" if r is row else "")

    def _reload_sources(self):
        """Re-read sources and push to the map without restarting."""
        self.sources = load_sources()
        self._rebuild_layer_rows()
        if self._page_ready:
            specs = [s.leaflet_spec() for s in self.sources.values() if s.bulk_ok]
            self._js(f"window.vm.setSources({json.dumps(specs)})")
        self._push_layers()

    def _current_view(self) -> tuple[float, float, int]:
        v = self.settings.value("view")
        lat, lon, z = (json.loads(v) if v else (35.2, -111.65, 11))
        return float(lat), float(lon), int(z)

    def _add_source(self):
        dlg = SourceDialog(self, set(self.sources), self._current_view())
        if dlg.exec() != QDialog.Accepted or not dlg.result_source:
            return
        user = load_user_sources()
        user.append(dlg.result_source)
        save_user_sources(user)
        self._reload_sources()
        for r in self.layer_rows:
            if r.src.id == dlg.result_source.id:
                r.check.setChecked(True)
                self._select_row(r)
        self.statusBar().showMessage(f"added source {dlg.result_source.id} → {user_sources_path()}", 6000)

    def _edit_source(self):
        row = getattr(self, "_selected_row", None)
        if not row or not row.src.user:
            QMessageBox.information(self, "Edit source", "Click the name of one of your own sources (marked ★) first. "
                                                          "Built-in sources can't be edited here.")
            return
        dlg = SourceDialog(self, set(self.sources) - {row.src.id}, self._current_view(), source=row.src)
        if dlg.exec() != QDialog.Accepted or not dlg.result_source:
            return
        user = [u for u in load_user_sources() if u.id != row.src.id] + [dlg.result_source]
        save_user_sources(user)
        self._reload_sources()

    def _remove_source(self):
        row = getattr(self, "_selected_row", None)
        if not row or not row.src.user:
            QMessageBox.information(self, "Remove source", "Click the name of one of your own sources (marked ★) first. "
                                                            "Built-in sources can't be removed.")
            return
        if QMessageBox.question(self, "Remove source", f"Remove '{row.src.name}' ({row.src.id})?\n"
                                                        "Its cached tiles stay in the cache folder until you clear it.") != QMessageBox.Yes:
            return
        save_user_sources([u for u in load_user_sources() if u.id != row.src.id])
        self._reload_sources()

    # ------------------------------------------------------------------ cache & sources
    def _refresh_cache_label(self):
        d = default_cache_dir()
        total = sum(f.stat().st_size for f in d.glob("*.sqlite")) if d.exists() else 0
        self.cache_label.setText(f"{d}\n{total / 1e6:.0f} MB in {len(list(d.glob('*.sqlite'))) if d.exists() else 0} source file(s)")

    def _open_cache(self):
        from PySide6.QtGui import QDesktopServices
        d = default_cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def _clear_cache(self):
        d = default_cache_dir()
        files = list(d.glob("*.sqlite*")) if d.exists() else []
        if not files:
            QMessageBox.information(self, "Tile cache", "The cache is already empty.")
            return
        if QMessageBox.question(self, "Clear tile cache",
                                f"Delete {len(files)} cache file(s) in\n{d}?\n\nThe next export re-downloads every tile.") != QMessageBox.Yes:
            return
        for f in files:
            try:
                f.unlink()
            except OSError as e:
                QMessageBox.warning(self, "Tile cache", f"{f.name}: {e}")
        self._refresh_cache_label()

    def _show_sources_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Tile sources")
        dlg.resize(760, 520)
        v = QVBoxLayout(dlg)
        txt = QPlainTextEdit()
        txt.setReadOnly(True)
        lines = []
        for src in self.sources.values():
            lines.append(f"{src.id}  —  {src.name}")
            lines.append(f"    kind {src.kind}   zoom {src.min_zoom}-{src.max_zoom}   "
                         f"{'base' if src.opaque else 'overlay'}   {'' if src.bulk_ok else 'PREVIEW ONLY (not offered)'}")
            lines.append(f"    rate {src.rate_limit} req/s, {src.max_workers} workers   {src.attribution}")
            lines.append(f"    {src.url}")
            if src.export_params:
                lines.append(f"    export params: {src.export_params}")
            if src.notes:
                lines.append(f"    {src.notes}")
            lines.append("")
        p = user_sources_path()
        lines.append(f"Custom / overriding sources file: {p}  ({'exists' if p.exists() else 'not created yet'})")
        lines.append("Use 'Add source…' in the Layers panel to add one with a live test. The file can also be edited by "
                     "hand (same fields as above); an entry whose id matches a built-in one replaces it. "
                     "Restart the app after editing the file by hand.")
        txt.setPlainText("\n".join(lines))
        v.addWidget(txt)
        row = QHBoxLayout()
        b = QPushButton("Add source…")
        b.clicked.connect(lambda: (dlg.accept(), self._add_source()))
        row.addWidget(b)
        b = QPushButton("Open sources file")
        b.clicked.connect(lambda: self._open_user_sources())
        row.addWidget(b)
        row.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        row.addWidget(bb)
        v.addLayout(row)
        dlg.exec()

    def _open_user_sources(self):
        from PySide6.QtGui import QDesktopServices
        p = user_sources_path()
        if not p.exists():
            write_user_sources_template(p)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    # ------------------------------------------------------------------ diagnostics
    def _test_sources(self):
        from ..tilemath import lonlat_to_tile
        v = self.settings.value("view")
        lat, lon, z = (json.loads(v) if v else (35.2, -111.65, 11))
        z = int(min(max(z, 8), 13))
        x, y = lonlat_to_tile(lon, lat, z)
        lines = [f"tile z{z} x{x} y{y} at {lat:.4f}, {lon:.4f}", ""]
        rows = [r for r in self.layer_rows if r.visible] or self.layer_rows[:1]
        for r in rows:
            f = TileFetcher(r.src, refresh=True, timeout=20, retries=0)
            res = f.fetch(x, y, z)
            if res.status == "ok":
                lines.append(f"OK      {r.src.id}: {len(res.data):,} bytes")
            else:
                lines.append(f"FAIL    {r.src.id}: {res.status} {res.error}")
            lines.append(f"        {r.src.tile_url(x, y, z)[:110]}")
        lines.append("")

        def show(stats):
            try:
                st = json.loads(stats) if stats else {}
            except Exception:  # noqa: BLE001
                st = {}
            lines.append(f"map page: {st.get('ok', '?')} tiles loaded, {st.get('err', '?')} failed"
                         + (f"  (last failure: {st.get('lastErr')})" if st.get('err') else ""))
            lines.append(f"page URL: {self.server.url('map.html')}")
            QMessageBox.information(self, "Tile source test", "\n".join(lines))

        if self._page_ready:
            self.view.page().runJavaScript("window.vm.tileStats()", show)
        else:
            show("")

    # ------------------------------------------------------------------ features
    def _refresh_feature_list(self):
        cur = self._selected_id()
        self.feature_list.blockSignals(True)
        self.feature_list.clear()
        for f in self.features:
            icon = {"line": "—", "point": "●", "polygon": "▱"}.get(f.kind, "?")
            it = QListWidgetItem(f"{icon} {f.name or f.id}")
            it.setData(Qt.UserRole, f.id)
            it.setForeground(QColor(f.style.get("color", "#ff0000")))
            self.feature_list.addItem(it)
            if f.id == cur:
                self.feature_list.setCurrentItem(it)
        self.feature_list.blockSignals(False)

    def _selected_id(self) -> Optional[str]:
        it = self.feature_list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _selected_feature(self) -> Optional[Feature]:
        fid = self._selected_id()
        return next((f for f in self.features if f.id == fid), None)

    @Slot(str)
    def _on_feature_selected(self, fid: str):
        for i in range(self.feature_list.count()):
            if self.feature_list.item(i).data(Qt.UserRole) == fid:
                self.feature_list.setCurrentRow(i)
                return

    def _on_feature_list_select(self, cur, _prev):
        f = self._selected_feature()
        self.style_box.setEnabled(f is not None)
        if not f:
            return
        self._loading_style = True
        self.f_name.setText(f.name)
        color = f.style.get("color", "#ff6600" if f.kind == "point" else "#ff0000")
        self.f_color.setStyleSheet(f"background:{color}")
        self.f_color.setText(color)
        self.f_width.setValue(float(f.style.get("size" if f.kind == "point" else "width",
                                                 10 if f.kind == "point" else 3)))
        self.f_label.setChecked(bool(f.style.get("label", True)))
        self.f_label.setEnabled(f.kind == "point")
        self.f_visible.setChecked(f.visible)
        self.f_minzoom.setValue(f.min_zoom)
        self._loading_style = False

    def _pick_color(self):
        f = self._selected_feature()
        if not f:
            return
        c = QColorDialog.getColor(QColor(self.f_color.text()), self, "Feature color")
        if c.isValid():
            self.f_color.setText(c.name())
            self.f_color.setStyleSheet(f"background:{c.name()}")
            self._apply_feature_style()

    def _apply_feature_style(self, *_):
        if getattr(self, "_loading_style", False):
            return
        f = self._selected_feature()
        if not f:
            return
        style = dict(f.style)
        style["color"] = self.f_color.text()
        if f.kind == "point":
            style["size"] = self.f_width.value()
            style["label"] = self.f_label.isChecked()
        else:
            style["width"] = self.f_width.value()
        props = {"name": self.f_name.text(), "style": style, "visible": self.f_visible.isChecked(),
                 "min_zoom": self.f_minzoom.value()}
        self._js(f"window.vm.updateFeature({json.dumps(f.id)}, {json.dumps(props)})")

    def _delete_selected(self):
        fid = self._selected_id()
        if fid:
            self._js(f"window.vm.removeFeature({json.dumps(fid)})")

    def _import_gpx(self):
        start = self.settings.value("last_dir", str(Path.home()))
        paths, _ = QFileDialog.getOpenFileNames(self, "Import GPX", start, "GPX (*.gpx)")
        if not paths:
            return
        self.settings.setValue("last_dir", str(Path(paths[0]).parent))
        new = []
        for p in paths:
            try:
                new += parse_gpx(p)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "GPX", f"{p}: {e}")
        existing = {f.id for f in self.features}
        for f in new:
            while f.id in existing:
                f.id += "_"
            existing.add(f.id)
        self.features += new
        self._push_features()
        bb = features_bbox(new)
        if bb:
            self._js(f"window.vm.zoomTo({bb.west},{bb.south},{bb.east},{bb.north})")

    def _push_features(self):
        self._js(f"window.vm.loadFeatures({json.dumps(features_to_geojson(self.features))})")
        self._refresh_feature_list()
        self._update_estimate()

    def _export_gpx(self):
        if not self.features:
            QMessageBox.information(self, "GPX", "No tracks or waypoints to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export GPX", self.settings.value("last_dir", ""), "GPX (*.gpx)")
        if path:
            write_gpx([f for f in self.features if f.visible], path)
            self.statusBar().showMessage(f"wrote {path}", 5000)

    # ------------------------------------------------------------------ project
    def _project_dict(self) -> dict:
        return {"version": 1, "features": features_to_geojson(self.features), "export_area": self.export_area,
                "layers": [{"id": r.src.id, "visible": r.visible, "opacity": r.opacity} for r in self.layer_rows],
                "export": {"name": self.e_name.text(), "zmin": self.e_zmin.value(), "zmax": self.e_zmax.value(),
                           "encoding": self.e_encoding.currentIndex(), "theme": self.e_theme.currentIndex(),
                           "snap": self.e_snap.value(), "snap_on": self.e_snap_on.isChecked(),
                           "trim": self.e_trim.isChecked(), "jpegq": self.e_jpegq.value(),
                           "colors": self.e_colors.value(), "write_empty": self.e_write_empty.isChecked(),
                           "extras": self.e_extras.isChecked(), "pad": self.e_pad.value(),
                           "desc": self.e_desc.text(), "both": self.e_both.isChecked()}}

    def _save_project(self):
        if not self.project_path:
            return self._save_project_as()
        self.project_path.write_text(json.dumps(self._project_dict(), indent=1))
        self.statusBar().showMessage(f"saved {self.project_path}", 4000)

    def _save_project_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save project", self.settings.value("last_dir", ""),
                                              "voymap project (*.voymap.json)")
        if path:
            self.project_path = Path(path)
            self._save_project()

    def _open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open project", self.settings.value("last_dir", ""),
                                              "voymap project (*.voymap.json);;GeoJSON (*.geojson *.json)")
        if not path:
            return
        d = json.loads(Path(path).read_text())
        self.project_path = Path(path)
        self.features = features_from_geojson(d.get("features", d))
        for lr in d.get("layers", []):
            for row in self.layer_rows:
                if row.src.id == lr["id"]:
                    row.check.setChecked(lr["visible"])
                    row.slider.setValue(int(lr["opacity"] * 100))
        e = d.get("export", {})
        if e:
            self.e_name.setText(e.get("name", self.e_name.text()))
            self.e_zmin.setValue(e.get("zmin", 8))
            self.e_zmax.setValue(e.get("zmax", 14))
            self.e_encoding.setCurrentIndex(e.get("encoding", 0))
            self.e_theme.setCurrentIndex(e.get("theme", 0))
            self.e_snap.setValue(e.get("snap", 11))
            self.e_snap_on.setChecked(e.get("snap_on", True))
            self.e_trim.setChecked(e.get("trim", False))
            self.e_jpegq.setValue(e.get("jpegq", 80))
            self.e_colors.setValue(e.get("colors", 256))
            self.e_write_empty.setChecked(e.get("write_empty", False))
            self.e_extras.setChecked(e.get("extras", False))
            self.e_pad.setValue(e.get("pad", 0.01))
            self.e_desc.setText(e.get("desc", "N/A"))
            self.e_both.setChecked(e.get("both", False))
        self._push_features()
        a = d.get("export_area")
        if a:
            try:
                ab = BBox.from_dict(a)
            except (KeyError, TypeError, ValueError):
                QMessageBox.warning(self, "Open project", "The project's export area is not four valid coordinates "
                                    "(west < east, south < north) and was ignored.")
            else:
                self._js(f"window.vm.setExportArea({ab.west},{ab.south},{ab.east},{ab.north})")
        bb = features_bbox(self.features)
        if bb:
            self._js(f"window.vm.zoomTo({bb.west},{bb.south},{bb.east},{bb.north})")

    # ------------------------------------------------------------------ export
    def _area_from_features(self):
        bb = features_bbox([f for f in self.features if f.visible])
        if not bb:
            QMessageBox.information(self, "Export area", "No tracks or waypoints loaded.")
            return
        pad = self.e_pad.value()
        bb = bb.buffered_deg(pad, pad)
        self._js(f"window.vm.setExportArea({bb.west},{bb.south},{bb.east},{bb.north})")

    def _current_bbox(self) -> Optional[BBox]:
        a = self.export_area
        if not a:
            return None
        try:
            return BBox(a["west"], a["south"], a["east"], a["north"])
        except ValueError:
            return None

    def _snap_zoom(self) -> Optional[int]:
        return self.e_snap.value() if self.e_snap_on.isChecked() else None

    def _snapped_bbox(self) -> Optional[BBox]:
        bb = self._current_bbox()
        if not bb:
            return None
        sz = self._snap_zoom()
        if sz is None:
            return bb
        return bb.snapped(min(max(sz, self.e_zmin.value()), self.e_zmax.value()))

    def _update_estimate(self):
        bb = self._current_bbox()
        if not bb:
            self.estimate_label.setText("")
            self.zoom_table.setText("")
            self._js("window.vm.showSnapped(null)")
            return
        zmin, zmax = self.e_zmin.value(), self.e_zmax.value()
        if zmax < zmin:
            self.estimate_label.setText("zoom max < zoom min")
            return
        sb = self._snapped_bbox()
        if sb != bb:
            self._js(f"window.vm.showSnapped({sb.west},{sb.south},{sb.east},{sb.north})")
        else:
            self._js("window.vm.showSnapped(null)")
        n = sb.total_tiles(zmin, zmax)
        self.zoom_table.setText("   ".join(f"z{z}:{sb.tile_count(z):,}" for z in range(zmin, zmax + 1)))
        layers = [r for r in self.layer_rows if r.visible and r.src.bulk_ok]
        self.estimate_label.setText(f"≈ {n:,} tiles, ≈ {n * AVG_TILE_BYTES / 1e6:.0f} MB, "
                                    f"{len(layers)} layer(s) → up to {n * len(layers):,} tile requests"
                                    + (f"\n(dotted outline = area snapped to the z{min(max(self._snap_zoom(), zmin), zmax)} grid)"
                                       if sb != bb else ""))

    def _build_spec(self) -> Optional[ExportSpec]:
        bb = self._current_bbox()
        if not bb:
            QMessageBox.warning(self, "Export", "Draw an export rectangle first.")
            return None
        layers = [LayerSpec(r.src.id, r.opacity) for r in self.layer_rows if r.visible]
        bad = [l.source_id for l in layers if not self.sources[l.source_id].bulk_ok]
        if bad:
            QMessageBox.warning(self, "Export", f"{', '.join(bad)} is preview-only (its usage policy forbids "
                                                "bulk download). Uncheck it to export.")
            return None
        if not layers and not self.features:
            QMessageBox.warning(self, "Export", "Select at least one layer.")
            return None
        if self.e_zmax.value() < self.e_zmin.value():
            QMessageBox.warning(self, "Export", "Zoom max is below zoom min.")
            return None
        start = self.settings.value("last_dir", str(Path.home()))
        path, _ = QFileDialog.getSaveFileName(self, "Save MBTiles", str(Path(start) / f"{self.e_name.text() or 'map'}.mbtiles"),
                                              "MBTiles (*.mbtiles)")
        if not path:
            return None
        self.settings.setValue("last_dir", str(Path(path).parent))
        enc = ("png8", "png", "jpeg")[self.e_encoding.currentIndex()]
        ring = self.export_area.get("ring")
        return ExportSpec(output=Path(path), name=self.e_name.text() or Path(path).stem, bbox=bb,
                          zmin=self.e_zmin.value(), zmax=self.e_zmax.value(), layers=layers,
                          features=[f for f in self.features if f.visible], encoding=enc,
                          polygon=[tuple(p) for p in ring] if ring else None,
                          refresh=self.e_refresh.isChecked(), spec_extras=self.e_extras.isChecked(),
                          snap_zoom=self._snap_zoom(), trim_coarse=self.e_trim.isChecked(),
                          jpeg_quality=self.e_jpegq.value(), png_colors=self.e_colors.value(),
                          skip_empty=not self.e_write_empty.isChecked(),
                          description=self.e_desc.text() or "N/A")

    def _theme_name(self) -> str:
        return THEMES[self.e_theme.currentIndex()]

    def _apply_theme_preview(self, *_):
        self._js(f"window.vm.setTheme({json.dumps(css_filter(self._theme_name()))})")

    def _start_export(self):
        spec = self._build_spec()
        if not spec:
            return
        spec.theme = self._theme_name()
        est = spec.estimate()
        if est["tiles"] > 20000:
            if QMessageBox.question(self, "Large export",
                                    f"{est['tiles']:,} tiles (~{est['bytes'] / 1e6:.0f} MB). Continue?") != QMessageBox.Yes:
                return
        specs = [spec.variant("day", "_day"), spec.variant("night", "_night")] if self.e_both.isChecked() else [spec]
        self.worker = ExportWorker(specs, self.sources)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_export_done)
        self.worker.failed.connect(self._on_export_failed)
        self.export_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setValue(0)
        self.overall_progress.setValue(0)
        self.overall_label.setText("Overall: starting…")
        self._file_index = 0
        self.worker.start()

    def _cancel_export(self):
        if self.worker:
            self.worker.cancel()
            self.progress_label.setText("cancelling…")

    @Slot(object)
    def _on_progress(self, p: Progress):
        import time
        self.progress.setMaximum(max(1, p.total))
        self.progress.setValue(p.done)
        self.progress_label.setText(f"[{p.step}: {p.phase}] {p.message}  errors={p.errors} missing={p.missing}"
                                    + (f"  {p.bytes / 1e6:.1f} MB" if p.bytes else ""))
        # overall across every file in this run (a day+night pair is two jobs of equal size)
        nfiles = max(1, len(self.worker.specs)) if self.worker else 1
        fi = self._file_index
        done = fi * p.overall_total + p.overall_done
        total = nfiles * max(1, p.overall_total)
        self.overall_progress.setMaximum(total)
        self.overall_progress.setValue(min(done, total))
        eta = ""
        if p.started_at and p.overall_done > 0 and p.overall_done < p.overall_total:
            rate = p.overall_done / max(1e-6, time.time() - p.started_at)
            remaining = (total - done) / rate
            eta = f"   ~{int(remaining // 60)} min {int(remaining % 60)} s left"
        label = f"Overall: {done:,} / {total:,} units"
        if nfiles > 1:
            label += f"   (file {fi + 1} of {nfiles})"
        self.overall_label.setText(label + eta)
        if p.phase in ("done", "cancelled") and fi + 1 < nfiles:
            self._file_index += 1

    @Slot(object)
    def _on_export_done(self, results):
        self.export_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self._refresh_cache_label()
        lines = []
        for spec, p in results:
            if p.phase == "done":
                info = inspect(spec.output)
                lines.append(f"{spec.output.name}: {p.written:,} tiles, {info['size_bytes'] / 1e6:.1f} MB "
                             f"(fetch errors {p.errors}, empty {p.missing})")
            else:
                lines.append(f"{spec.output.name}: {p.phase} — {p.message}")
        if results and all(p.phase == "done" for _, p in results):
            QMessageBox.information(self, "Export complete", "\n".join(lines) +
                                    f"\n\nFolder: {results[0][0].output.parent}\n\n"
                                    "Copy the file(s) to the Voyager Pro microSD card, then on the device: "
                                    "map screen → CUSTOM MAPS → IMPORT A NEW FILE.")
        else:
            self.progress_label.setText("\n".join(lines) or "cancelled")

    @Slot(str)
    def _on_export_failed(self, msg: str):
        self.export_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        QMessageBox.critical(self, "Export failed", msg)

    def _inspect_mbtiles(self):
        path, _ = QFileDialog.getOpenFileName(self, "Inspect MBTiles", self.settings.value("last_dir", ""), "MBTiles (*.mbtiles)")
        if path:
            QMessageBox.information(self, "MBTiles", format_inspection(inspect(path)))

    def closeEvent(self, ev):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)
        self.server.stop()
        super().closeEvent(ev)
