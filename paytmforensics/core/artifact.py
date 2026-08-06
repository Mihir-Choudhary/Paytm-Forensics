"""Artifact discovery & classification.

Locates the known Paytm stores inside an extraction and classifies each by kind so the
right parser can be dispatched. Tolerates missing files (version drift / partial images).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Known SQLite databases under /databases (filename -> logical name)
KNOWN_DBS = {
    "AppLocale.db", "PaytmMessageDatabase", "RealtimeSmsUploadDb", "appManagerDB",
    "bank_app_manager_database", "bank_signal", "cache_database", "chatDb.db",
    "com.google.android.datatransport.events", "contacts", "discoveryDb.db",
    "google_app_measurement_local.db", "pai_push_signal", "pai_signal", "passbook.db",
    "paytm_error_analytics", "paytmbank_error_analytics", "search_db",
    "storefront_db_try3", "ups_database",
}

# Encrypted / TEE-bound stores we only catalogue (FR-12)
ENCRYPTED_PREFS = {"Data.xml", "DataUPI.xml", "DataERUPEE.xml"}


@dataclass
class Artifact:
    rel_path: str
    abs_path: str
    kind: str            # sqlite | prefs_xml | json_cache | leveldb | datastore | other
    logical: str         # logical/known name


def _kind_for(rel: str, name: str) -> str:
    low = name.lower()
    if rel.startswith("databases/") and name in KNOWN_DBS:
        return "sqlite"
    if name in ("androidx.work.workdb", "Cookies", "Web Data"):
        return "sqlite"
    if rel.startswith("shared_prefs/") and low.endswith(".xml"):
        return "prefs_xml"
    if rel.startswith("shared_jsons/") and low.endswith(".json"):
        return "json_cache"
    if "local storage/leveldb" in rel.lower() or "session storage" in rel.lower():
        return "leveldb"
    if rel.startswith("files/datastore"):
        return "datastore"
    return "other"


def discover(root: str) -> list[Artifact]:
    """Walk the extraction and return classified artifacts. Read-only."""
    root = os.path.abspath(root)
    out: list[Artifact] = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            # skip SQLite side-files (we open the main db read-only)
            if name.endswith(("-shm", "-journal")):
                continue
            kind = _kind_for(rel, name)
            out.append(Artifact(rel, full, kind, name))
    out.sort(key=lambda a: a.rel_path)
    return out


def by_logical(arts: list[Artifact]) -> dict[str, Artifact]:
    """Index sqlite/known artifacts by logical name for quick parser lookup."""
    idx: dict[str, Artifact] = {}
    for a in arts:
        idx.setdefault(a.logical, a)
    return idx
