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

hiddenimports = collect_submodules("paytmforensics.parsers")

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
