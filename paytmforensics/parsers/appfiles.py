"""PRD §5.1 `files/` and WebView artifacts that previously had no parser.

  * `files/datastore/*.preferences_pb`     — Jetpack DataStore (protobuf)
  * `files/PersistedInstallation*.json`    — Firebase installation ID + auth token
  * `files/AppEventsLogger.persistedevents`— Facebook SDK queued events (Java-serialized)
  * `files/frc_*.json`                     — Firebase Remote Config fetch/activate state
  * `app_webview/Default/IndexedDB/**`     — best-effort LevelDB string recovery
  * `app_webview/Default/Web Data`         — Chromium autofill/web database

Everything here is labelled best-effort where the format is not fully decoded, and token
values are redacted to length-only (these files carry live credentials).
"""
from __future__ import annotations

import json
import os
import re
from typing import Iterator

from .base import BaseParser, register
from ..core.models import AppStateItem, PrefItem, Record, WebStorageItem
from ..enrich import protobuf, timestamps
from ..enrich.secrets import redact_text, redact_value
from ..ingest import sqlite_ro as sql

_PRINTABLE = re.compile(rb"[\x20-\x7e]{5,300}")
#: keys whose VALUE is a credential; surfaced as length-only
_SECRET_KEYS = re.compile(r"(token|secret|auth|key|credential)", re.I)


def _redact(key: str, value):
    return redact_value(key, value)


@register
class DataStoreParser(BaseParser):
    """Jetpack DataStore `*.preferences_pb` — protobuf-encoded preferences."""
    name = "files.datastore"
    needs = ()

    def __init__(self, artifacts, hashes, all_artifacts=None):
        super().__init__(artifacts, hashes, all_artifacts)
        self._files = [a for a in self.all_artifacts
                       if a.rel_path.startswith("files/datastore")
                       or a.rel_path.endswith(".preferences_pb")]

    def available(self) -> bool:
        return bool(self._files)

    def parse(self) -> Iterator[Record]:
        for art in sorted(self._files, key=lambda a: a.rel_path):
            try:
                with open(art.abs_path, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            fields = protobuf.decode(raw)
            strings = [m.group(0).decode("ascii", "ignore") for m in _PRINTABLE.finditer(raw)]
            yield PrefItem(
                provenance=self.prov(art, None, confidence=0.6),
                raw={"decoded": fields},
                key=os.path.basename(art.rel_path),
                value=redact_text(json.dumps(fields, ensure_ascii=False)[:400] if fields
                                  else " | ".join(strings[:10]) or "(empty)"),
                pref_file="files/datastore (protobuf, best-effort)",
            )


@register
class FirebaseInstallationParser(BaseParser):
    """`PersistedInstallation*.json` — Firebase installation ID (a stable device/app id)."""
    name = "files.firebase_installation"
    needs = ()

    def __init__(self, artifacts, hashes, all_artifacts=None):
        super().__init__(artifacts, hashes, all_artifacts)
        self._files = [a for a in self.all_artifacts if "PersistedInstallation" in a.rel_path]

    def available(self) -> bool:
        return bool(self._files)

    def parse(self) -> Iterator[Record]:
        for art in sorted(self._files, key=lambda a: a.rel_path):
            try:
                with open(art.abs_path, encoding="utf-8", errors="replace") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(d, dict):
                continue
            for k, v in sorted(d.items()):
                yield PrefItem(
                    provenance=self.prov(art, None),
                    raw={"key": k},
                    key=k, value=_redact(k, v),
                    pref_file=os.path.basename(art.rel_path),
                )


@register
class RemoteConfigParser(BaseParser):
    """`frc_*.json` — Firebase Remote Config fetch/activate state."""
    name = "files.remote_config"
    needs = ()

    def __init__(self, artifacts, hashes, all_artifacts=None):
        super().__init__(artifacts, hashes, all_artifacts)
        self._files = [a for a in self.all_artifacts
                       if os.path.basename(a.rel_path).startswith("frc_")
                       and a.rel_path.endswith(".json")]

    def available(self) -> bool:
        return bool(self._files)

    def parse(self) -> Iterator[Record]:
        from ..enrich import config_explain
        from ..core.models import ConfigItem
        for art in sorted(self._files, key=lambda a: a.rel_path):
            try:
                with open(art.abs_path, encoding="utf-8", errors="replace") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            flags = d.get("configs_key") if isinstance(d, dict) else None
            if not isinstance(flags, dict):
                flags = {k: v for k, v in (d or {}).items()
                         if isinstance(v, (str, int, float, bool))}
            for k, v in sorted(flags.items()):
                kind, meaning = config_explain.explain(k, v)
                yield ConfigItem(
                    provenance=self.prov(art, None),
                    raw={"key": k},
                    key=k, value=str(v), kind=kind, meaning=meaning,
                )


@register
class FacebookEventsParser(BaseParser):
    """`AppEventsLogger.persistedevents` — Facebook SDK events queued for upload.

    Java-serialized; we surface the readable strings and redact the access token.
    """
    name = "files.fb_events"
    needs = ("AppEventsLogger.persistedevents",)

    def parse(self) -> Iterator[Record]:
        art = self.get("AppEventsLogger.persistedevents")
        if not art:
            return
        try:
            with open(art.abs_path, "rb") as f:
                raw = f.read()
        except OSError:
            return
        strings = [m.group(0).decode("ascii", "ignore") for m in _PRINTABLE.finditer(raw)]
        # the access token follows the accessTokenString marker; never emit it verbatim
        redacted = []
        for i, s in enumerate(strings):
            prev = strings[i - 1] if i else ""
            if "accessToken" in prev or "accessToken" in s:
                redacted.append(f"<redacted:{len(s)} chars>")
            else:
                redacted.append(s)
        yield AppStateItem(
            provenance=self.prov(art, None, confidence=0.5),
            raw={"strings": redacted[:60]},
            key="facebook_sdk_queued_events",
            value="Java-serialized Facebook SDK event queue (best-effort string inventory); "
                  "any access token is redacted",
            detail=" | ".join(redacted[:10]),
        )


@register
class IndexedDbParser(BaseParser):
    """WebView IndexedDB — best-effort LevelDB string recovery (PRD §5.1, best-effort)."""
    name = "webview.indexeddb"
    needs = ()

    def __init__(self, artifacts, hashes, all_artifacts=None):
        super().__init__(artifacts, hashes, all_artifacts)
        self._files = [a for a in self.all_artifacts
                       if "indexeddb" in a.rel_path.lower()
                       and a.rel_path.lower().endswith((".ldb", ".log"))]

    def available(self) -> bool:
        return bool(self._files)

    def parse(self) -> Iterator[Record]:
        for art in sorted(self._files, key=lambda a: a.rel_path):
            try:
                with open(art.abs_path, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            # origin is encoded in the directory name: https_host_0.indexeddb.leveldb
            m = re.search(r"(https?_[^/]+?)_\d+\.indexeddb", art.rel_path, re.I)
            origin = m.group(1).replace("_", "://", 1) if m else art.rel_path
            seen = set()
            for mm in _PRINTABLE.finditer(raw):
                s = mm.group(0).decode("ascii", "ignore").strip()
                if len(s) < 8 or s in seen:
                    continue
                seen.add(s)
                if len(seen) > 200:
                    break
                yield WebStorageItem(
                    provenance=self.prov(art, None, confidence=0.4),
                    raw={}, origin=origin, key=os.path.basename(art.rel_path),
                    value=redact_text(s)[:300], store_kind="indexed_db", best_effort=True,
                )


@register
class WebDataParser(BaseParser):
    """`app_webview/Default/Web Data` — Chromium autofill/web database."""
    name = "webview.webdata"
    needs = ("Web Data",)

    def parse(self) -> Iterator[Record]:
        art = self.get("Web Data")
        if not art:
            return
        with self.open(art) as con:
            for table in sql.list_tables(con):
                for rowid, r in sql.rows(con, table):
                    vals = {k: v for k, v in r.items() if v not in (None, "")}
                    if not vals:
                        continue
                    yield AppStateItem(
                        provenance=self.prov(art, table, rowid),
                        raw=r,
                        key=f"webdata.{table}",
                        value=redact_text(
                            json.dumps(vals, ensure_ascii=False, default=str))[:400],
                    )
