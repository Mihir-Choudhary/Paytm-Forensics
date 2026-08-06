# PyInstaller spec for PaytmForensics (onedir build).
# Build:  pyinstaller paytmforensics.spec
# Produces dist/PaytmForensics/PaytmForensics(.exe) with bundled decode tables.

# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

datas = [
    ("paytmforensics/resources/error_mapper.json", "paytmforensics/resources"),
    ("paytmforensics/resources/bankappmanager_defaults.json", "paytmforensics/resources"),
    ("paytmforensics/resources/leaflet/leaflet.js", "paytmforensics/resources/leaflet"),
    ("paytmforensics/resources/leaflet/leaflet.css", "paytmforensics/resources/leaflet"),
    ("paytmforensics/resources/ifsc_bank_codes.json", "paytmforensics/resources"),
]

# Every subpackage that is imported LAZILY (inside a function) — the carver, the
# correlation and report layers, the enrich helpers, the GUI views and the privacy
# module are all imported on demand from Case/MainWindow. Collect them explicitly so a
# packaged build cannot silently lose, say, the carver or the report generator.
hiddenimports = []
for _pkg in ("paytmforensics.parsers", "paytmforensics.carving", "paytmforensics.correlate",
             "paytmforensics.core", "paytmforensics.enrich", "paytmforensics.ingest",
             "paytmforensics.report", "paytmforensics.gui"):
    hiddenimports += collect_submodules(_pkg)
# optional at runtime, but needed for the interactive map / Qt PDF path
hiddenimports += ["PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore",
                  "PySide6.QtPrintSupport"]

a = Analysis(
    ["run_gui.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="PaytmForensics", debug=False, strip=False, upx=False, console=False,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="PaytmForensics",
)
