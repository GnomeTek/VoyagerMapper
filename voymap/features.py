"""Vector features (tracks, waypoints, polygons) with styling, plus GPX and
GeoJSON import/export. The GUI exchanges GeoJSON with the Leaflet page; GPX is
the interchange format with the Voyager Pro and CalTopo.

Coordinates are always (lon, lat) tuples in WGS84.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Optional

from .tilemath import BBox

GPX_NS = "http://www.topografix.com/GPX/1/1"
GPX_NS_10 = "http://www.topografix.com/GPX/1/0"


@dataclass
class LineStyle:
    color: str = "#ff0000"     # CSS hex
    width: float = 3.0         # pixels at any zoom (constant screen width)
    opacity: float = 1.0
    dash: Optional[list[int]] = None  # e.g. [8, 6]


@dataclass
class MarkerStyle:
    color: str = "#ff6600"
    size: float = 10.0         # marker diameter in pixels
    label: bool = True         # draw the name next to the marker
    label_size: int = 12       # font px


@dataclass
class PolygonStyle:
    color: str = "#0066ff"
    width: float = 2.0
    opacity: float = 1.0
    fill_opacity: float = 0.15


@dataclass
class Feature:
    id: str
    kind: str                       # "line" | "point" | "polygon"
    name: str = ""
    # line: list of segments, each list of (lon, lat); point: [[(lon,lat)]];
    # polygon: list of rings (first = outer)
    coords: list[list[tuple[float, float]]] = field(default_factory=list)
    style: dict = field(default_factory=dict)
    properties: dict = field(default_factory=dict)
    visible: bool = True
    min_zoom: int = 0               # don't draw below this zoom (0 = always)

    def line_style(self) -> LineStyle:
        return LineStyle(**{k: v for k, v in self.style.items() if k in LineStyle.__dataclass_fields__})

    def marker_style(self) -> MarkerStyle:
        return MarkerStyle(**{k: v for k, v in self.style.items() if k in MarkerStyle.__dataclass_fields__})

    def polygon_style(self) -> PolygonStyle:
        return PolygonStyle(**{k: v for k, v in self.style.items() if k in PolygonStyle.__dataclass_fields__})

    def bbox(self) -> Optional[BBox]:
        pts = [p for seg in self.coords for p in seg]
        if not pts:
            return None
        lons = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        w, e = min(lons), max(lons)
        s, n = min(lats), max(lats)
        eps = 1e-9
        return BBox(w, s, max(e, w + eps), max(n, s + eps))

    @property
    def point(self) -> tuple[float, float]:
        return self.coords[0][0]


# ----------------------------------------------------------------------------
# GPX
# ----------------------------------------------------------------------------

def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _child_text(el: ET.Element, name: str) -> str:
    for c in el:
        if _strip_ns(c.tag) == name and c.text:
            return c.text.strip()
    return ""


def parse_gpx(path: str | Path, id_prefix: str = "") -> list[Feature]:
    """Parse tracks, routes and waypoints from a GPX 1.0/1.1 file."""
    tree = ET.parse(str(path))
    root = tree.getroot()
    feats: list[Feature] = []
    prefix = id_prefix or Path(path).stem
    n = 0

    for el in root:
        tag = _strip_ns(el.tag)
        if tag == "wpt":
            n += 1
            lon, lat = float(el.get("lon")), float(el.get("lat"))
            name = _child_text(el, "name")
            feats.append(Feature(
                id=f"{prefix}-wpt-{n}", kind="point", name=name,
                coords=[[(lon, lat)]],
                properties={k: _child_text(el, k) for k in ("desc", "sym", "type", "ele") if _child_text(el, k)},
            ))
        elif tag == "trk":
            n += 1
            name = _child_text(el, "name")
            segs = []
            for seg in el:
                if _strip_ns(seg.tag) != "trkseg":
                    continue
                pts = [(float(p.get("lon")), float(p.get("lat")))
                       for p in seg if _strip_ns(p.tag) == "trkpt"]
                if len(pts) >= 2:
                    segs.append(pts)
            if segs:
                feats.append(Feature(id=f"{prefix}-trk-{n}", kind="line", name=name, coords=segs,
                                     properties={"source": "trk"}))
        elif tag == "rte":
            n += 1
            name = _child_text(el, "name")
            pts = [(float(p.get("lon")), float(p.get("lat")))
                   for p in el if _strip_ns(p.tag) == "rtept"]
            if len(pts) >= 2:
                feats.append(Feature(id=f"{prefix}-rte-{n}", kind="line", name=name, coords=[pts],
                                     properties={"source": "rte"}))
    return feats


def write_gpx(features: Iterable[Feature], path: str | Path, creator: str = "voymap") -> None:
    """Write features to GPX 1.1. Polygons are written as closed tracks (GPX has
    no polygon type; the Voyager Pro will show them as track outlines)."""
    ET.register_namespace("", GPX_NS)
    gpx = ET.Element(f"{{{GPX_NS}}}gpx", version="1.1", creator=creator)
    feats = list(features)
    for f in feats:
        if f.kind == "point":
            lon, lat = f.point
            w = ET.SubElement(gpx, f"{{{GPX_NS}}}wpt", lat=f"{lat:.7f}", lon=f"{lon:.7f}")
            if "ele" in f.properties:
                ET.SubElement(w, f"{{{GPX_NS}}}ele").text = str(f.properties["ele"])
            ET.SubElement(w, f"{{{GPX_NS}}}name").text = f.name or f.id
            if f.properties.get("desc"):
                ET.SubElement(w, f"{{{GPX_NS}}}desc").text = f.properties["desc"]
            if f.properties.get("sym"):
                ET.SubElement(w, f"{{{GPX_NS}}}sym").text = f.properties["sym"]
    for f in feats:
        if f.kind in ("line", "polygon"):
            t = ET.SubElement(gpx, f"{{{GPX_NS}}}trk")
            ET.SubElement(t, f"{{{GPX_NS}}}name").text = f.name or f.id
            for seg in f.coords:
                pts = list(seg)
                if f.kind == "polygon" and pts and pts[0] != pts[-1]:
                    pts.append(pts[0])
                s = ET.SubElement(t, f"{{{GPX_NS}}}trkseg")
                for lon, lat in pts:
                    ET.SubElement(s, f"{{{GPX_NS}}}trkpt", lat=f"{lat:.7f}", lon=f"{lon:.7f}")
    ET.indent(gpx)
    ET.ElementTree(gpx).write(str(path), encoding="utf-8", xml_declaration=True)


# ----------------------------------------------------------------------------
# GeoJSON (GUI interchange + project files)
# ----------------------------------------------------------------------------

def features_to_geojson(features: Iterable[Feature]) -> dict:
    out = []
    for f in features:
        if f.kind == "point":
            geom = {"type": "Point", "coordinates": list(f.point)}
        elif f.kind == "line":
            if len(f.coords) == 1:
                geom = {"type": "LineString", "coordinates": [list(p) for p in f.coords[0]]}
            else:
                geom = {"type": "MultiLineString", "coordinates": [[list(p) for p in s] for s in f.coords]}
        elif f.kind == "polygon":
            geom = {"type": "Polygon", "coordinates": [[list(p) for p in r] for r in f.coords]}
        else:
            continue
        out.append({
            "type": "Feature",
            "id": f.id,
            "geometry": geom,
            "properties": {"name": f.name, "style": f.style, "visible": f.visible,
                           "min_zoom": f.min_zoom, **f.properties},
        })
    return {"type": "FeatureCollection", "features": out}


def features_from_geojson(gj: dict) -> list[Feature]:
    feats = []
    for i, gf in enumerate(gj.get("features", [])):
        geom = gf.get("geometry") or {}
        props = dict(gf.get("properties") or {})
        style = props.pop("style", {}) or {}
        name = props.pop("name", "") or ""
        visible = bool(props.pop("visible", True))
        min_zoom = int(props.pop("min_zoom", 0) or 0)
        fid = str(gf.get("id") or props.pop("id", None) or f"f{i}")
        gt = geom.get("type")
        c = geom.get("coordinates")
        if gt == "Point":
            feats.append(Feature(fid, "point", name, [[(c[0], c[1])]], style, props, visible, min_zoom))
        elif gt == "LineString":
            feats.append(Feature(fid, "line", name, [[(p[0], p[1]) for p in c]], style, props, visible, min_zoom))
        elif gt == "MultiLineString":
            feats.append(Feature(fid, "line", name, [[(p[0], p[1]) for p in s] for s in c], style, props, visible, min_zoom))
        elif gt == "Polygon":
            feats.append(Feature(fid, "polygon", name, [[(p[0], p[1]) for p in r] for r in c], style, props, visible, min_zoom))
        elif gt == "MultiPolygon":
            for j, poly in enumerate(c):
                feats.append(Feature(f"{fid}-{j}", "polygon", name, [[(p[0], p[1]) for p in r] for r in poly], style, props, visible, min_zoom))
    return feats


def save_geojson(features: Iterable[Feature], path: str | Path) -> None:
    Path(path).write_text(json.dumps(features_to_geojson(features), indent=1))


def load_geojson(path: str | Path) -> list[Feature]:
    return features_from_geojson(json.loads(Path(path).read_text()))


def features_bbox(features: Iterable[Feature]) -> Optional[BBox]:
    boxes = [b for b in (f.bbox() for f in features) if b]
    if not boxes:
        return None
    return BBox(min(b.west for b in boxes), min(b.south for b in boxes),
                max(b.east for b in boxes), max(b.north for b in boxes))
