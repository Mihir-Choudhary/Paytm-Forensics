"""Tests for the map view + chat refinements."""
import os
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from paytmforensics.core.case import Case

EXTRACTION = os.environ.get(
    "PAYTM_EXTRACTION", r"C:\Users\admin\Documents\Projects\paytm\net.one97.paytm")


@pytest.fixture(scope="module")
def case_dir():
    if not os.path.isdir(EXTRACTION):
        pytest.skip("no extraction")
    out = tempfile.mkdtemp(prefix="ptmfx_m8_")
    c = Case(EXTRACTION, out, case_id="M8")
    c.ingest(); c.parse_all(); c.carve(); c.correlate(); c.build_timeline(); c.close()
    return out


def test_mapview_collects_fixes(case_dir):
    from PySide6.QtWidgets import QApplication
    from paytmforensics.gui.datasource import DataSource
    from paytmforensics.gui.mapview import MapView, _fixes
    QApplication.instance() or QApplication([])
    ds = DataSource(os.path.join(case_dir, "case.db"))
    fixes = _fixes(ds)
    assert len(fixes) > 0
    assert all("lat" in f and "lon" in f and "src" in f for f in fixes)
    mv = MapView(ds)                       # constructs without error
    assert mv.fixes == fixes
    ds.close()


def test_map_html_has_markers_and_path(case_dir):
    from paytmforensics.gui.datasource import DataSource
    from paytmforensics.gui.mapview import build_map_html, _fixes
    ds = DataSource(os.path.join(case_dir, "case.db"))
    fixes = _fixes(ds)
    html = build_map_html(fixes)
    assert "L.map(" in html and "circleMarker" in html      # leaflet markers
    assert "polyline" in html                                # movement path
    assert "cartocdn.com/dark_all" in html                  # dark basemap tiles
    assert f"{fixes[0]['lat']:.5f}"[:6] in html or str(fixes[0]["lat"])[:6] in html
    ds.close()


def test_map_canvas_offline_fallback_renders():
    from PySide6.QtWidgets import QApplication
    from paytmforensics.gui.mapview import MapCanvas
    QApplication.instance() or QApplication([])
    canvas = MapCanvas([{"lat": 12.97, "lon": 79.16, "src": "signal", "ts": "2024", "info": ""}])
    canvas.resize(400, 300)
    pm = canvas.grab()                     # offline scatter paints without crash
    assert pm.width() > 0


def test_leaflet_bundled():
    from paytmforensics.resources_util import resource_path
    assert os.path.exists(resource_path("leaflet/leaflet.js"))
    assert os.path.exists(resource_path("leaflet/leaflet.css"))


def test_chat_status_classifier():
    from paytmforensics.gui.chatview import ChatView
    f = ChatView._status
    assert f({"msg_type": "TRANSFER"})[2] == "success"
    assert f({"msg_type": "TRANSFER_FAIL"})[2] == "failed"
    assert f({"msg_type": "UPI_REQUEST"})[2] == "requested"
    assert f({"msg_type": "UPI_RESPONSE", "content": "Payment request declined"})[2] == "declined"


def test_chat_view_builds_with_dates(case_dir):
    from PySide6.QtWidgets import QApplication
    from paytmforensics.gui.datasource import DataSource
    from paytmforensics.gui.chatview import ChatView
    QApplication.instance() or QApplication([])
    ds = DataSource(os.path.join(case_dir, "case.db"))
    cv = ChatView(ds)
    assert cv.clist.count() > 0            # conversations present
    cv.clist.setCurrentRow(0)              # triggers _show_convo (date seps + bubbles)
    assert cv.thread.count() > 1
    ds.close()


def test_map_html_light_vs_dark(case_dir):
    from paytmforensics.gui.datasource import DataSource
    from paytmforensics.gui.mapview import build_map_html, _fixes
    ds = DataSource(os.path.join(case_dir, "case.db"))
    fixes = _fixes(ds)
    assert "dark_all" in build_map_html(fixes, dark=True)
    assert "light_all" in build_map_html(fixes, dark=False)
    ds.close()


def test_theme_toggle(case_dir):
    from PySide6.QtWidgets import QApplication
    from paytmforensics.gui.app import MainWindow
    from paytmforensics.gui import theme
    theme.set_theme("dark")                       # known start state
    QApplication.instance() or QApplication([])
    win = MainWindow(case_dir)
    assert theme.current_theme() == "dark"
    dark_qss = theme.qss()
    win._toggle_theme()
    assert theme.current_theme() == "light"
    assert theme.qss() != dark_qss                # palette actually changed
    assert "Dark" in win.theme_btn.text()         # button now offers dark
    win._toggle_theme()
    assert theme.current_theme() == "dark"
    win.close()


def test_map_nav_present_in_window(case_dir):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from paytmforensics.gui.app import MainWindow
    QApplication.instance() or QApplication([])
    win = MainWindow(case_dir)
    doms = {win.nav.item(i).data(Qt.UserRole) for i in range(win.nav.count())}
    assert "map" in doms
    # selecting map shows the map page without error
    for i in range(win.nav.count()):
        if win.nav.item(i).data(Qt.UserRole) == "map":
            win.nav.setCurrentRow(i); break
    assert win._map_page is not None
    win.close()
