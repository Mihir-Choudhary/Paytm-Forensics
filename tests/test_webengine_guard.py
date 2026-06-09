"""Regression tests for graceful WebEngine degradation (2026-06-10).

QtWebEngine can import yet abort the process on use (missing Chromium resource
packs, no sandbox, locked-down forensic workstation / CI). Both consumers — the
map view and the HTML→PDF fallback — must skip WebEngine when PAYTM_NO_WEBMAP is
set instead of crashing, falling back to the offline scatter / a clear error.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_webengine_available_honours_env(monkeypatch):
    from paytmforensics.gui import mapview
    monkeypatch.setenv("PAYTM_NO_WEBMAP", "1")
    assert mapview.webengine_available() is False


def test_pdf_via_qt_skips_when_disabled(monkeypatch, tmp_path):
    from paytmforensics.report import html
    monkeypatch.setenv("PAYTM_NO_WEBMAP", "1")
    out = str(tmp_path / "x.pdf")
    # must return False (not crash, not write) so generate() can surface a clean error
    assert html._pdf_via_qt("<html><body>hi</body></html>", out) is False
    assert not os.path.exists(out)


def test_mapview_uses_offline_canvas_when_disabled(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from paytmforensics.gui.mapview import MapView
    monkeypatch.setenv("PAYTM_NO_WEBMAP", "1")
    QApplication.instance() or QApplication([])
    fixes = [{"lat": 12.97, "lon": 79.16, "src": "signal", "ts": "2024", "info": ""}]
    mv = MapView(_make_ds(fixes))
    assert mv._is_web is False
    assert hasattr(mv, "canvas"), "must fall back to the offline MapCanvas"


def _make_ds(fixes):
    """A tiny DataSource stand-in returning location rows from `fixes`."""
    class _DS:
        def load(self, dom):
            if dom != "location":
                return []
            return [{"latitude": f["lat"], "longitude": f["lon"],
                     "source_kind": f["src"], "timestamp": {"utc_iso": f["ts"]},
                     "pincode": f["info"]} for f in fixes]
    return _DS()


if __name__ == "__main__":
    import sys, traceback

    class _MP:
        """Minimal monkeypatch shim for the standalone (no-pytest) runner."""
        def __init__(self):
            self._saved = []
        def setenv(self, k, v):
            self._saved.append((k, os.environ.get(k)))
            os.environ[k] = v
        def undo(self):
            for k, v in reversed(self._saved):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    import tempfile
    failed = 0
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        mp = _MP()
        try:
            import inspect
            kw = {}
            params = inspect.signature(fn).parameters
            if "monkeypatch" in params:
                kw["monkeypatch"] = mp
            if "tmp_path" in params:
                import pathlib
                kw["tmp_path"] = pathlib.Path(tempfile.mkdtemp())
            fn(**kw)
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
        finally:
            mp.undo()
    sys.exit(1 if failed else 0)
