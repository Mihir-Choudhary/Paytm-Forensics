"""Evidence integrity: hashing, manifest, read-only enforcement (EV-1, EV-2, EV-6).

Computes SHA-256 + MD5 for every input file, builds a manifest, and can re-verify the
extraction against a prior manifest to prove nothing changed between runs.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, asdict
from typing import Optional

_CHUNK = 1024 * 1024


@dataclass
class FileHash:
    rel_path: str
    size: int
    sha256: str
    md5: str


def hash_file(path: str) -> tuple[str, str, int]:
    """Return (sha256, md5, size) streaming the file; never opens for write."""
    h256 = hashlib.sha256()
    hmd5 = hashlib.md5()
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            h256.update(chunk)
            hmd5.update(chunk)
    return h256.hexdigest(), hmd5.hexdigest(), size


def build_manifest(root: str) -> dict:
    """Hash every file under `root` (recursively). Read-only."""
    entries: list[dict] = []
    root = os.path.abspath(root)
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            try:
                sha, md5, size = hash_file(full)
                entries.append(asdict(FileHash(rel.replace("\\", "/"), size, sha, md5)))
            except OSError as e:
                entries.append({"rel_path": rel.replace("\\", "/"), "error": str(e)})
    entries.sort(key=lambda e: e["rel_path"])           # stable ordering (EV-6)
    return {"root": root, "file_count": len(entries), "files": entries}


def manifest_index(manifest: dict) -> dict[str, dict]:
    return {e["rel_path"]: e for e in manifest.get("files", [])}


def verify_against(manifest: dict, root: str) -> dict:
    """Re-hash `root` and compare to `manifest`. Returns a verification report."""
    current = build_manifest(root)
    old = manifest_index(manifest)
    new = manifest_index(current)
    changed, missing, added = [], [], []
    for path, oe in old.items():
        ne = new.get(path)
        if ne is None:
            missing.append(path)
        elif oe.get("sha256") != ne.get("sha256"):
            changed.append(path)
    for path in new:
        if path not in old:
            added.append(path)
    ok = not (changed or missing or added)
    return {"ok": ok, "changed": changed, "missing": missing, "added": added}


def save_manifest(manifest: dict, out_path: str) -> None:
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def load_manifest(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
