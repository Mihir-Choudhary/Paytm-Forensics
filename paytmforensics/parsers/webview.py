"""M7 (best-effort): WebView cookies + LevelDB Local/Session Storage extraction.

- Cookies: full parse of the Chromium Cookies SQLite (all cookies, not just geo).
- LevelDB: Chromium localStorage is stored in a LevelDB. A correct LevelDB reader needs
  native libs (plyvel), unavailable on Windows here, so we do a *best-effort* raw scan of
  the .ldb/.log files extracting origin/key/value strings. Results are flagged best_effort.
"""
from __future__ import annotations

import glob
import json
import os
import re
from typing import Iterator

from .base import BaseParser, register
from ..core.artifact import Artifact
from ..core.models import Cookie, WebStorageItem, WebCacheEntry, Record
from ..ingest import sqlite_ro as sql
from ..enrich import timestamps

_URL_RE = re.compile(rb"https?://[\x21-\x7e]{6,300}")


@register
class CookiesParser(BaseParser):
    name = "webview.cookies"
    needs = ("Cookies",)

    def parse(self) -> Iterator[Record]:
        art = self.get("Cookies")
        if not art:
            return
        with sql.open_ro(art.abs_path) as con:
            tabs = [t.lower() for t in sql.list_tables(con)]
            if "cookies" not in tabs:
                return
            for rowid, r in sql.rows(con, "cookies"):
                yield Cookie(
                    provenance=self.prov(art, "cookies", rowid),
                    raw={k: r.get(k) for k in ("host_key", "name", "path")},
                    host=r.get("host_key"),
                    name=r.get("name"),
                    value=r.get("value"),
                    is_secure=str(r.get("is_secure")) == "1",
                    is_httponly=str(r.get("is_httponly")) == "1",
                    created=timestamps.decode(r.get("creation_utc"), hint="webkit_us").to_dict(),
                    expires=timestamps.decode(r.get("expires_utc"), hint="webkit_us").to_dict(),
                )


# Chromium localStorage leveldb key layout (simplified):
#   "_" + origin + "\x00" + key   ->   value
_LS_KEY = re.compile(rb"_(https?://[^\x00]{3,120})\x00\x01?([\x20-\x7e]{1,120})")
_PRINTABLE = re.compile(rb"[\x20-\x7e]{4,200}")


@register
class WebCacheParser(BaseParser):
    """Inventory JSON API responses cached by the WebView Service Worker.

    Chromium stores each cached response body in a `*_0` file (request URL + headers are
    appended as binary). We surface only JSON-bodied responses (skipping JS bundles/HTML/
    images), capturing the body preview, any embedded userId, and the request URL.
    """
    name = "webview.cache"
    needs = ()

    def __init__(self, artifacts, hashes, all_artifacts=None):
        super().__init__(artifacts, hashes, all_artifacts)
        self._entries = [a for a in self.all_artifacts
                         if "service worker/cachestorage" in a.rel_path.lower()
                         and a.rel_path.endswith("_0")]

    def available(self) -> bool:
        return bool(self._entries)

    def parse(self) -> Iterator[Record]:
        dec = json.JSONDecoder()
        for art in self._entries:
            try:
                if os.path.getsize(art.abs_path) > 3_000_000:
                    continue
                with open(art.abs_path, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            txt = data.decode("utf-8", "ignore")
            i = txt.find("{")
            if i < 0 or i > 200:
                continue                                  # not a JSON-bodied response
            head = txt[:i].lower()
            if "<html" in txt[:200].lower() or "function(" in txt[:i + 60]:
                continue                                  # HTML / JS bundle
            try:
                obj, end = dec.raw_decode(txt[i:])
            except (ValueError, json.JSONDecodeError):
                continue
            if not isinstance(obj, (dict, list)):
                continue
            # request URL is in the trailing binary
            m = _URL_RE.search(data[i + end:])
            url = m.group(0).decode("ascii", "ignore") if m else None
            uid = obj.get("userId") if isinstance(obj, dict) else None
            created = obj.get("created") if isinstance(obj, dict) else None
            yield WebCacheEntry(
                provenance=self.prov(art, None, confidence=0.7),
                raw={},
                url=url,
                method=(obj.get("method") if isinstance(obj, dict) else None),
                user_id=uid,
                size=len(data),
                body_preview=json.dumps(obj, ensure_ascii=False)[:400],
                created=timestamps.decode(created).to_dict() if created else None,
            )


@register
class LocalStorageParser(BaseParser):
    name = "webview.localstorage"
    # discovery indexes leveldb files individually; we locate them via the case root instead
    needs = ()

    def __init__(self, artifacts, hashes, all_artifacts=None):
        super().__init__(artifacts, hashes, all_artifacts)
        # find any leveldb file artifact to anchor the webview dir
        self._ldb_arts = [a for a in self.all_artifacts
                          if a.rel_path.lower().endswith((".ldb", ".log"))
                          and "storage" in a.rel_path.lower()]

    def available(self) -> bool:
        return bool(self._ldb_arts)

    def parse(self) -> Iterator[Record]:
        for art in self._ldb_arts:
            kind = "session_storage" if "session storage" in art.rel_path.lower() else "local_storage"
            try:
                with open(art.abs_path, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            seen = set()
            for m in _LS_KEY.finditer(data):
                origin = m.group(1).decode("ascii", "replace")
                key = m.group(2).decode("ascii", "replace")
                # value: take printable run immediately after the match (best-effort)
                tail = data[m.end():m.end() + 220]
                vm = _PRINTABLE.search(tail)
                value = vm.group(0).decode("ascii", "replace") if vm else None
                sig = (origin, key)
                if sig in seen:
                    continue
                seen.add(sig)
                yield WebStorageItem(
                    provenance=self.prov(art, None, confidence=0.5),
                    raw={}, origin=origin, key=key, value=value,
                    store_kind=kind, best_effort=True,
                )
