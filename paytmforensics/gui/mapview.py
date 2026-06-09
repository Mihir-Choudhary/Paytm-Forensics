"""Location view: a real interactive map (Leaflet + dark basemap tiles) with GPS markers,
a chronological movement path, and a synced fixes list.

Forensic note: parsing/evidence handling is 100% offline. The *basemap tiles* are fetched
from the network only for visualization (no evidence leaves the host beyond the coordinate
tiles requested). If WebEngine/tiles are unavailable, an offline geo-scatter is shown.
Leaflet JS/CSS are bundled locally; only tiles require a connection.
"""
from __future__ import annotations

import json
import os
import tempfile

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem, QLabel, QFrame
)
from PySide6.QtCore import Qt, QPointF, QRectF, QUrl
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont

from .datasource import DataSource
from .theme import C
from ..resources_util import resource_path

SRC_COLOR = {"signal": "#f0883e", "cookie": "#f85149", "diagnostic": "#2f81f7"}


def _fixes(ds: DataSource) -> list[dict]:
    out = []
    for r in ds.load("location"):
        if r.get("latitude") is not None and r.get("longitude") is not None:
            out.append({"lat": r["latitude"], "lon": r["longitude"],
                        "src": r.get("source_kind") or "signal",
                        "ts": (r.get("timestamp") or {}).get("utc_iso") or "",
                        "info": str(r.get("pincode") or "")})
    for r in ds.load("diagnostic"):
        if r.get("latitude") is not None and r.get("longitude") is not None:
            out.append({"lat": r["latitude"], "lon": r["longitude"], "src": "diagnostic",
                        "ts": (r.get("timestamp") or {}).get("utc_iso") or "",
                        "info": str(r.get("message") or "")})
    out.sort(key=lambda p: p["ts"])
    return out


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def webengine_available() -> bool:
    """Whether the interactive (Leaflet) map can be used.

    QtWebEngine may be importable yet non-functional — e.g. a forensic
    workstation/CI box missing the Chromium resource packs, where constructing
    a view aborts the process. Setting PAYTM_NO_WEBMAP=1 forces the offline
    scatter fallback so the GUI degrades gracefully instead of crashing.
    """
    if os.environ.get("PAYTM_NO_WEBMAP"):
        return False
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        return True
    except Exception:
        return False


def _unlink_quiet(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def build_map_html(fixes: list[dict], dark: bool = True) -> str:
    """Self-contained HTML embedding Leaflet + markers + movement path."""
    leaflet_css = _read(resource_path("leaflet/leaflet.css"))
    leaflet_js = _read(resource_path("leaflet/leaflet.js"))
    data = json.dumps(fixes)
    colors = json.dumps(SRC_COLOR)
    tile = "dark_all" if dark else "light_all"
    page_bg = "#0d1117" if dark else "#eef1f5"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{leaflet_css}</style>
<style>
 html,body,#map {{ height:100%; margin:0; background:{page_bg}; }}
 .leaflet-popup-content {{ font-family:Segoe UI,Arial; font-size:12px; }}
 .leaflet-control-attribution {{ font-size:9px; }}
</style></head><body><div id="map"></div>
<script>{leaflet_js}</script>
<script>
 var fixes = {data}, COL = {colors};
 var map = L.map('map', {{zoomControl:true}});
 L.tileLayer('https://{{s}}.basemaps.cartocdn.com/{tile}/{{z}}/{{x}}/{{y}}{{r}}.png', {{
    attribution:'© OpenStreetMap © CARTO', maxZoom:19, subdomains:'abcd'
 }}).addTo(map);
 var markers = [], pts = [];
 fixes.forEach(function(f, i) {{
    var c = COL[f.src] || '#8b949e';
    var m = L.circleMarker([f.lat, f.lon], {{radius:7, color:'#fff', weight:1,
        fillColor:c, fillOpacity:0.9}}).addTo(map);
    var when = (f.ts||'').replace('T',' ').substring(0,19);
    m.bindPopup('<b>'+f.src+'</b><br>'+f.lat.toFixed(5)+', '+f.lon.toFixed(5)+
                '<br>'+when+(f.info?('<br>'+f.info):''));
    markers.push(m); pts.push([f.lat, f.lon]);
 }});
 if (pts.length > 1) {{
    L.polyline(pts, {{color:'#2f81f7', weight:2, opacity:0.5, dashArray:'5,6'}}).addTo(map);
 }}
 if (pts.length) {{ map.fitBounds(pts, {{padding:[40,40]}}); }}
 else {{ map.setView([20.5,78.9], 5); }}   // India
 function focusFix(i) {{
    if (i>=0 && i<markers.length) {{ map.setView(markers[i].getLatLng(), 14);
        markers[i].openPopup(); }}
 }}
</script></body></html>"""


# ----------------------------- offline fallback ---------------------------- #
class MapCanvas(QWidget):
    """Offline geo-scatter fallback (no tiles)."""
    def __init__(self, fixes):
        super().__init__()
        self.fixes = fixes; self.setMinimumSize(480, 360); self.setMouseTracking(True)
        self._hover = -1; self._sel = -1; self._bounds()

    def _bounds(self):
        if self.fixes:
            lats = [p["lat"] for p in self.fixes]; lons = [p["lon"] for p in self.fixes]
            self.min_lat, self.max_lat = min(lats), max(lats)
            self.min_lon, self.max_lon = min(lons), max(lons)
        else:
            self.min_lat = self.max_lat = self.min_lon = self.max_lon = 0
        if self.max_lat - self.min_lat < 0.02: self.min_lat -= .01; self.max_lat += .01
        if self.max_lon - self.min_lon < 0.02: self.min_lon -= .01; self.max_lon += .01

    def select(self, i): self._sel = i; self.update()

    def _xy(self, lat, lon, m, w, h):
        sx = (lon - self.min_lon) / (self.max_lon - self.min_lon)
        sy = (self.max_lat - lat) / (self.max_lat - self.min_lat)
        return m + sx * (w - 2 * m), m + sy * (h - 2 * m)

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height(); m = 44
        p.fillRect(self.rect(), QColor(C["bg_input"]))
        p.setPen(QPen(QColor(C["border"]), 1)); p.drawRect(QRectF(m, m, w - 2 * m, h - 2 * m))
        if not self.fixes:
            p.setPen(QColor(C["text_muted"])); p.drawText(self.rect(), Qt.AlignCenter, "No GPS points"); return
        for i, pt in enumerate(self.fixes):
            x, y = self._xy(pt["lat"], pt["lon"], m, w, h)
            col = QColor(SRC_COLOR.get(pt["src"], "#8b949e"))
            r = 9 if i == self._sel else 6
            p.setPen(QPen(QColor("white") if i == self._sel else col.darker(140), 2 if i == self._sel else 1))
            col.setAlpha(200); p.setBrush(QBrush(col)); p.drawEllipse(QPointF(x, y), r, r)


class MapView(QWidget):
    def __init__(self, ds: DataSource):
        super().__init__()
        self.fixes = _fixes(ds)
        lay = QHBoxLayout(self); lay.setContentsMargins(20, 16, 20, 16); lay.setSpacing(12)

        left = QFrame(); left.setObjectName("card")
        lv = QVBoxLayout(left); lv.setContentsMargins(14, 12, 14, 12)
        srcs = {}
        for f in self.fixes:
            srcs[f["src"]] = srcs.get(f["src"], 0) + 1
        legend = "   ".join(f"● {k} ({v})" for k, v in srcs.items())
        head = QLabel(f"Location map — {len(self.fixes)} GPS fixes      {legend}")
        head.setObjectName("cardTitle"); lv.addWidget(head)
        self.web = self._make_map()
        lv.addWidget(self.web, 1)
        note = QLabel("Interactive map (dark basemap tiles fetched online for display; "
                      "evidence parsing is fully offline). Dashed line = movement path.")
        note.setObjectName("kvKey"); note.setStyleSheet(f"color:{C['text_dim']};"); note.setWordWrap(True)
        lv.addWidget(note)
        lay.addWidget(left, 1)

        right = QFrame(); right.setObjectName("card"); right.setMaximumWidth(360)
        rv = QVBoxLayout(right); rv.setContentsMargins(12, 12, 12, 12)
        rv.addWidget(QLabel("Fixes (chronological)"))
        self.list = QListWidget(); self.list.setObjectName("nav")
        for i, f in enumerate(self.fixes):
            it = QListWidgetItem(f"{f['ts'][:19].replace('T',' ')}\n   {f['lat']:.5f}, {f['lon']:.5f}  ·  {f['src']}")
            it.setData(Qt.UserRole, i); self.list.addItem(it)
        self.list.currentItemChanged.connect(self._focus)
        rv.addWidget(self.list)
        lay.addWidget(right)

    def _make_map(self):
        """Return a Leaflet QWebEngineView, or the offline scatter on failure."""
        if not webengine_available():
            self._is_web = False
            self.canvas = MapCanvas(self.fixes)
            return self.canvas
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            from .theme import current_theme
            view = QWebEngineView()
            html = build_map_html(self.fixes, dark=(current_theme() == "dark"))
            # setHtml() silently drops content above Qt's 2 MB limit (inline Leaflet
            # + thousands of fixes exceed it) — load from a temp file instead
            fd, path = tempfile.mkstemp(prefix="ptmfx_map_", suffix=".html")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(html)
            # free-function cleanup: must not touch self, which may already be
            # mid-destruction when the child's destroyed signal fires
            view.destroyed.connect(lambda *_, p=path: _unlink_quiet(p))
            view.setUrl(QUrl.fromLocalFile(path))
            self._is_web = True
            return view
        except Exception:
            self._is_web = False
            self.canvas = MapCanvas(self.fixes)
            return self.canvas

    def _focus(self, cur, _prev):
        if not cur:
            return
        i = cur.data(Qt.UserRole)
        if getattr(self, "_is_web", False):
            self.web.page().runJavaScript(f"focusFix({i});")
        elif hasattr(self, "canvas"):
            self.canvas.select(i)
