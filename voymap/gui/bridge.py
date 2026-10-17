"""QWebChannel bridge: slots the Leaflet page calls, re-emitted as Qt signals.

Slot names are what the JS calls (``bridge.featuresChanged(...)``); the Qt
signals carry a ``sig_`` prefix so the two namespaces don't collide.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class Bridge(QObject):
    sig_ready = Signal()
    sig_features = Signal(str)        # GeoJSON FeatureCollection text
    sig_export_area = Signal(str)     # JSON {west,south,east,north,ring} or "null"
    sig_selected = Signal(str)
    sig_view = Signal(float, float, int)
    sig_log = Signal(str)

    @Slot()
    def ready(self):
        self.sig_ready.emit()

    @Slot(str)
    def featuresChanged(self, gj: str):
        self.sig_features.emit(gj)

    @Slot(str)
    def exportAreaChanged(self, js: str):
        self.sig_export_area.emit(js)

    @Slot(str)
    def featureSelected(self, fid: str):
        self.sig_selected.emit(fid)

    @Slot(float, float, int)
    def viewChanged(self, lat: float, lon: float, z: int):
        self.sig_view.emit(lat, lon, z)

    @Slot(str)
    def log(self, msg: str):
        self.sig_log.emit(msg)
