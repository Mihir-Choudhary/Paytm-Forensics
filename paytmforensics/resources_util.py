"""Resource path resolution that works both in source and in a PyInstaller bundle."""
from __future__ import annotations

import os
import sys


def resource_path(name: str) -> str:
    """Return absolute path to a bundled resource file under resources/."""
    # PyInstaller unpacks data files under sys._MEIPASS
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cand = os.path.join(base, "paytmforensics", "resources", name)
        if os.path.exists(cand):
            return cand
        cand = os.path.join(base, "resources", name)
        if os.path.exists(cand):
            return cand
    return os.path.join(os.path.dirname(__file__), "resources", name)
