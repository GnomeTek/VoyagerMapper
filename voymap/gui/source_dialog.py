"""Add / edit a tile source from the app, with a one-tile test before saving."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..fetch import TileFetcher
from ..sources import TileSource
from ..tilemath import lonlat_to_tile

HELP = {
    "xyz": ("URL template with {z}, {x}, {y} and optionally {s} for a subdomain letter. "
            "Examples:\n"
            "  https://tile.example.org/{z}/{x}/{y}.png\n"
            "  https://{s}.tile.example.org/{z}/{x}/{y}.png   (subdomains: abc)\n"
            "  ArcGIS tile caches use .../MapServer/tile/{z}/{y}/{x}  (note y before x)"),
    "arcgis_export": ("Dynamic ArcGIS MapServer URL ending in /MapServer (the app adds /export). "
                      "One render request per tile, so these are slow; pick a modest rate.\n"
                      "Optional export params as JSON, e.g. {\"layers\": \"show:1,2\"} to limit layers."),
}


class _TestWorker(QThread):
    done = Signal(str, bool)

    def __init__(self, src: TileSource, lat: float, lon: float, z: int):
        super().__init__()
        self.src, self.lat, self.lon, self.z = src, lat, lon, z

    def run(self):
        z = min(max(self.z, self.src.min_zoom), self.src.max_zoom)
        x, y = lonlat_to_tile(self.lon, self.lat, z)
        url = self.src.tile_url(x, y, z)
        try:
            f = TileFetcher(self.src, refresh=True, timeout=20, retries=0)
            r = f.fetch(x, y, z)
        except Exception as e:  # noqa: BLE001
            self.done.emit(f"error: {e}\n{url}", False)
            return
        if r.status == "ok":
            from io import BytesIO
            from PIL import Image
            try:
                im = Image.open(BytesIO(r.data))
                desc = f"{im.format} {im.size[0]}x{im.size[1]} {im.mode}"
            except Exception:  # noqa: BLE001
                desc = "not a decodable image"
            self.done.emit(f"OK — z{z} x{x} y{y}: {len(r.data):,} bytes, {desc}\n{url}", True)
        else:
            self.done.emit(f"FAILED — z{z} x{x} y{y}: {r.status} {r.error}\n{url}", False)


class SourceDialog(QDialog):
    """Returns a validated TileSource via .result_source after accept()."""

    def __init__(self, parent: QWidget, existing_ids: set[str], view: tuple[float, float, int],
                 source: Optional[TileSource] = None):
        super().__init__(parent)
        self.setWindowTitle("Edit tile source" if source else "Add tile source")
        self.resize(640, 560)
        self.existing_ids = existing_ids
        self.view = view
        self.editing = source
        self.result_source: Optional[TileSource] = None
        self._worker: Optional[_TestWorker] = None

        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.f_name = QLineEdit(source.name if source else "")
        self.f_name.setPlaceholderText("Shown in the Layers panel, e.g. 'State Trust Land'")
        form.addRow("Name", self.f_name)
        self.f_id = QLineEdit(source.id if source else "")
        self.f_id.setPlaceholderText("auto from name; a-z 0-9 _ -")
        self.f_id.setEnabled(source is None)
        form.addRow("Id", self.f_id)
        self.f_kind = QComboBox()
        self.f_kind.addItem("XYZ tile server (URL template)", "xyz")
        self.f_kind.addItem("ArcGIS MapServer (dynamic export)", "arcgis_export")
        if source:
            self.f_kind.setCurrentIndex(0 if source.kind == "xyz" else 1)
        form.addRow("Type", self.f_kind)
        self.f_url = QLineEdit(source.url if source else "")
        form.addRow("URL", self.f_url)
        self.help = QLabel()
        self.help.setWordWrap(True)
        self.help.setStyleSheet("color:#555; font-size:11px; font-family: monospace")
        form.addRow("", self.help)
        self.f_sub = QLineEdit(source.subdomains if source else "")
        self.f_sub.setPlaceholderText("abc  (only if the URL has {s})")
        form.addRow("Subdomains", self.f_sub)
        self.f_params = QLineEdit(json.dumps(source.export_params) if source and source.export_params else "")
        self.f_params.setPlaceholderText('{"layers": "show:1,2"}')
        form.addRow("Export params", self.f_params)
        self.f_attr = QLineEdit(source.attribution if source else "")
        form.addRow("Attribution", self.f_attr)
        zrow = QHBoxLayout()
        self.f_zmin = QSpinBox(); self.f_zmin.setRange(0, 22); self.f_zmin.setValue(source.min_zoom if source else 0)
        self.f_zmax = QSpinBox(); self.f_zmax.setRange(0, 22); self.f_zmax.setValue(source.max_zoom if source else 16)
        zrow.addWidget(self.f_zmin); zrow.addWidget(QLabel("to")); zrow.addWidget(self.f_zmax); zrow.addStretch()
        form.addRow("Zoom range", zrow)
        self.f_opaque = QComboBox()
        self.f_opaque.addItem("Base map (opaque)", True)
        self.f_opaque.addItem("Overlay (transparent)", False)
        if source and not source.opaque:
            self.f_opaque.setCurrentIndex(1)
        form.addRow("Role", self.f_opaque)
        rrow = QHBoxLayout()
        self.f_rate = QDoubleSpinBox(); self.f_rate.setRange(0.1, 50); self.f_rate.setDecimals(1)
        self.f_rate.setValue(source.rate_limit if source else 4.0); self.f_rate.setSuffix(" req/s")
        self.f_workers = QSpinBox(); self.f_workers.setRange(1, 16); self.f_workers.setValue(source.max_workers if source else 3)
        rrow.addWidget(self.f_rate); rrow.addWidget(QLabel("workers")); rrow.addWidget(self.f_workers); rrow.addStretch()
        form.addRow("Rate limit", rrow)
        self.f_notes = QPlainTextEdit(source.notes if source else "")
        self.f_notes.setFixedHeight(56)
        form.addRow("Notes", self.f_notes)
        lay.addLayout(form)

        policy = QLabel("Check the server's usage terms before bulk-downloading from it. Many free tile servers "
                        "forbid it (OpenStreetMap's do); government services such as USGS and USFS allow it. "
                        "Keep the rate limit modest for community-run servers.")
        policy.setWordWrap(True)
        policy.setStyleSheet("color:#7a4a00; font-size:11px")
        lay.addWidget(policy)

        trow = QHBoxLayout()
        self.test_btn = QPushButton("Test: fetch one tile at the current map view")
        self.test_btn.clicked.connect(self._test)
        trow.addWidget(self.test_btn)
        lay.addLayout(trow)
        self.test_out = QLabel("")
        self.test_out.setWordWrap(True)
        self.test_out.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.test_out.setStyleSheet("font-family: monospace; font-size: 11px")
        lay.addWidget(self.test_out)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self.f_kind.currentIndexChanged.connect(self._kind_changed)
        self.f_name.textEdited.connect(self._auto_id)
        self._kind_changed()

    # ---- helpers
    def _kind_changed(self):
        kind = self.f_kind.currentData()
        self.help.setText(HELP[kind])
        self.f_sub.setEnabled(kind == "xyz")
        self.f_params.setEnabled(kind == "arcgis_export")

    def _auto_id(self, text: str):
        if self.editing is None:
            slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]
            self.f_id.setText(slug)

    def _build(self) -> Optional[TileSource]:
        params = {}
        if self.f_kind.currentData() == "arcgis_export" and self.f_params.text().strip():
            try:
                params = json.loads(self.f_params.text())
                if not isinstance(params, dict):
                    raise ValueError("must be a JSON object")
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Export params", f"Not valid JSON: {e}")
                return None
        src = TileSource(
            id=self.f_id.text().strip(), name=self.f_name.text().strip(), kind=self.f_kind.currentData(),
            url=self.f_url.text().strip(), attribution=self.f_attr.text().strip(),
            min_zoom=self.f_zmin.value(), max_zoom=self.f_zmax.value(), opaque=bool(self.f_opaque.currentData()),
            bulk_ok=True, rate_limit=self.f_rate.value(), max_workers=self.f_workers.value(),
            subdomains=self.f_sub.text().strip() if self.f_kind.currentData() == "xyz" else "",
            export_params=params, notes=self.f_notes.toPlainText().strip(), user=True,
        )
        errs = src.validate()
        if self.editing is None and src.id in self.existing_ids:
            errs.append(f"id {src.id!r} is already used; pick another (a built-in id can't be replaced from here)")
        if errs:
            QMessageBox.warning(self, "Source", "Please fix:\n• " + "\n• ".join(errs))
            return None
        return src

    def _test(self):
        src = self._build()
        if not src:
            return
        self.test_btn.setEnabled(False)
        self.test_out.setText("fetching…")
        lat, lon, z = self.view
        self._worker = _TestWorker(src, lat, lon, z)
        self._worker.done.connect(self._test_done)
        self._worker.start()

    def _test_done(self, msg: str, ok: bool):
        self.test_btn.setEnabled(True)
        self.test_out.setStyleSheet("font-family: monospace; font-size: 11px; color: %s" % ("#060" if ok else "#900"))
        self.test_out.setText(msg)

    def _accept(self):
        src = self._build()
        if src:
            self.result_source = src
            self.accept()
