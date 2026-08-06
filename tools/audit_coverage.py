#!/usr/bin/env python3
"""Artifact coverage audit: which files in the extraction does the tool actually READ?

Instruments builtins.open and sqlite3.connect for the duration of a full pipeline run and
records every path touched, then classifies the whole extraction into
read / discovered-but-never-opened, and cross-checks against the PRD's in-scope list.

This answers "is everything parsed?" at the FILE level. tools/audit_extraction.py answers
it at the SQLite-table level; neither alone is sufficient.

Usage:  python3 tools/audit_coverage.py <extraction> <out_dir>
"""
from __future__ import annotations

import builtins
import io
import json
import os
import sqlite3
import sys
import tempfile
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

H = "=" * 100


def section(t):
    print(f"\n{H}\n{t}\n{H}")


#: Files that exist but can carry no user data. Excluded from the coverage denominator so
#: the metric measures EVIDENCE coverage. Justification per entry:
#:   CURRENT/LOCK/LOG/LOG.old/MANIFEST-*  LevelDB bookkeeping (manifest pointer, lock,
#:                                        operational log) - no records
#:   *.pma                                Chrome BrowserMetrics scratch
#:   oat/*                                compiled dex
#:   *-shm                                SQLite shared-memory index, rebuilt from the WAL
#:   AppLocale.db                         UI translation strings (13k rows, not evidence)
def _non_evidential(rel: str) -> bool:
    base = os.path.basename(rel)
    if base in ("CURRENT", "LOCK", "LOG", "LOG.old") or base.startswith("MANIFEST-"):
        return True
    if rel.endswith((".pma", "-shm")) or rel.startswith("oat/"):
        return True
    if base == "AppLocale.db" or base.endswith("-no-backup"):
        return True
    return False


# PRD 5.1 in-scope artifact groups -> a predicate over the relative path
PRD_SCOPE = {
    "databases/* (SQLite)":      lambda r: r.startswith("databases/") and not r.endswith(("-wal", "-shm", "-journal")),
    "no_backup/workdb":          lambda r: r.startswith("no_backup/") and not r.endswith(("-wal", "-shm", "-journal")),
    "shared_prefs/*.xml":        lambda r: r.startswith("shared_prefs/") and r.endswith(".xml"),
    "shared_jsons/*.json":       lambda r: r.startswith("shared_jsons/") and r.endswith(".json"),
    "files/datastore/*":         lambda r: r.startswith("files/datastore"),
    "files/in_app_notification": lambda r: "in_app_notification_model" in r,
    "files/PersistedInstallation": lambda r: "PersistedInstallation" in r,
    "files/AppEventsLogger":     lambda r: "AppEventsLogger" in r,
    "app_webview cookies/webdata": lambda r: r.endswith(("Default/Cookies", "Default/Web Data")),
    "app_webview Local Storage": lambda r: "local storage" in r.lower(),
    "app_webview Session Storage": lambda r: "session storage" in r.lower(),
    "app_webview IndexedDB":     lambda r: "indexeddb" in r.lower(),
}


def main():
    ext = os.path.abspath(sys.argv[1])
    out = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else tempfile.mkdtemp(prefix="cov_")

    all_files = []
    for dp, _d, fs in os.walk(ext):
        for f in fs:
            all_files.append(os.path.relpath(os.path.join(dp, f), ext).replace("\\", "/"))
    all_files.sort()

    opened: set[str] = set()
    _open, _connect = builtins.open, sqlite3.connect

    def track(p):
        try:
            ap = os.path.abspath(str(p))
            if ap.startswith(ext):
                opened.add(os.path.relpath(ap, ext).replace("\\", "/"))
        except Exception:
            pass

    def my_open(file, *a, **k):
        track(file)
        return _open(file, *a, **k)

    def my_connect(database, *a, **k):
        d = str(database)
        if d.startswith("file:"):
            from urllib.parse import urlparse, unquote
            d = unquote(urlparse(d).path)
        track(d)
        return _connect(database, *a, **k)

    # --- run the full pipeline under instrumentation ----------------------- #
    builtins.open, sqlite3.connect = my_open, my_connect
    try:
        from paytmforensics.core.case import Case
        c = Case(ext, out, case_id="COV", examiner="cov", evidence_number="COV")
        c.ingest()                      # hashes every file - excluded below
        hashed = set(opened)            # everything ingest touched
        opened.clear()
        results = c.parse_all()
        parsed_files = set(opened)
        opened.clear()
        c.carve()
        carved_files = set(opened)
        c.correlate(); c.build_timeline(); c.close()
    finally:
        builtins.open, sqlite3.connect = _open, _connect

    section("1. FILE-LEVEL COVERAGE  (which files does any PARSER actually open?)")
    # The carver sniffs magic bytes on every artifact to find databases, which would
    # inflate "read" to ~100% and mean nothing. Coverage is measured on PARSER reads;
    # the carver is reported separately and only counted where it really carved.
    read = parsed_files
    never = [f for f in all_files if f not in read]
    never_ev = [f for f in never if not _non_evidential(f)]
    print(f"  files in extraction            : {len(all_files):>5}")
    print(f"  hashed into the manifest       : {len(hashed):>5}  (integrity only, not parsed)")
    print(f"  opened by a PARSER             : {len(parsed_files):>5}")
    print(f"  touched by the carver          : {len(carved_files):>5}  "
          f"(includes magic-byte sniffing; NOT counted as parsed)")
    print(f"  READ BY NO PARSER              : {len(never):>5}   "
          f"({len(never)/len(all_files)*100:.1f}%)")
    ev_total = len([f for f in all_files if not _non_evidential(f)])
    print(f"  ...of which EVIDENCE-BEARING   : {len(never_ev):>5}   "
          f"({len(never_ev)/ev_total*100:.1f}% of {ev_total} evidence-bearing files)")
    if never_ev:
        print("\n  evidence-bearing files no parser reads:")
        for f in sorted(never_ev)[:25]:
            print(f"      {f}")

    section("2. UNREAD FILES BY GROUP")
    grp = defaultdict(list)
    for f in never:
        if f.endswith(("-wal", "-shm", "-journal")):
            grp["sqlite side-files (incl. the WAL - see F-01)"].append(f)
        elif f.startswith("app_webview/"):
            grp["app_webview/*"].append(f)
        elif f.startswith("shared_prefs/"):
            grp["shared_prefs/*.xml"].append(f)
        elif f.startswith("shared_jsons/"):
            grp["shared_jsons/*.json"].append(f)
        elif f.startswith("databases/"):
            grp["databases/*"].append(f)
        elif f.startswith("files/"):
            grp["files/*"].append(f)
        elif f.startswith("oat/"):
            grp["oat/* (compiled dex - not evidence)"].append(f)
        else:
            grp["other"].append(f)
    for k in sorted(grp, key=lambda x: -len(grp[x])):
        v = grp[k]
        print(f"\n  {k}: {len(v)} unread")
        for f in sorted(v)[:14]:
            try:
                sz = os.path.getsize(os.path.join(ext, f))
            except OSError:
                sz = 0
            print(f"      {sz:>10,}B  {f}")
        if len(v) > 14:
            print(f"      … and {len(v)-14} more")

    section("3. PRD 5.1 IN-SCOPE GROUPS  (G1: 'parse 100% of plaintext artifacts')")
    for label, pred in PRD_SCOPE.items():
        members = [f for f in all_files if pred(f) and not _non_evidential(f)]
        got = [f for f in members if f in read]
        if not members:
            print(f"  {label:<30} absent from this extraction")
            continue
        pct = len(got) / len(members) * 100
        mark = "ok  " if pct == 100 else ("FAIL" if pct == 0 else "part")
        print(f"  [{mark}] {label:<30} {len(got):>3}/{len(members):<3} files read ({pct:5.1f}%)")

    section("4. shared_prefs — which of the XML files are surfaced?")
    from paytmforensics.parsers.prefs import INTEREST
    sp = [f for f in all_files if f.startswith("shared_prefs/") and f.endswith(".xml")]
    print(f"  shared_prefs XML files present : {len(sp)}")
    print(f"  whitelisted in prefs.INTEREST  : {len(INTEREST)}")
    got = [f for f in sp if os.path.basename(f) in INTEREST]
    print(f"  actually parsed                : {len(got)}")
    print(f"\n  NOT parsed ({len(sp)-len(got)}):")
    for f in sorted(sp):
        if os.path.basename(f) not in INTEREST:
            print(f"      {os.path.getsize(os.path.join(ext,f)):>9,}B  {os.path.basename(f)}")

    section("5. shared_jsons — catalogued vs present (FR-12)")
    sj = [f for f in all_files if f.startswith("shared_jsons/")]
    con = sqlite3.connect(os.path.join(out, "case.db"))
    enc = [json.loads(x) for (x,) in con.execute(
        "SELECT data FROM records WHERE domain='encrypted'")]
    names = {e.get("name") for e in enc}
    print(f"  shared_jsons files present     : {len(sj)}")
    print(f"  catalogued as encrypted        : {len([n for n in names if str(n).endswith('.json')])}")
    print(f"  neither parsed nor catalogued  : "
          f"{len([f for f in sj if os.path.basename(f) not in names and f not in read])}")
    for f in sorted(sj):
        b = os.path.basename(f)
        state = "catalogued" if b in names else ("read" if f in read else "IGNORED")
        if state == "IGNORED":
            print(f"      {os.path.getsize(os.path.join(ext,f)):>9,}B  {b}")
    con.close()

    section("6. PRD FUNCTIONAL REQUIREMENTS WITH NO PARSER AT ALL")
    from paytmforensics.parsers import REGISTRY
    needs = set()
    for cls in REGISTRY:
        needs |= set(getattr(cls, "needs", ()))
    checks = [
        ("FR-3  contacts / contacts_phones / enrichment_data", "contacts",
         any("contacts" == n for n in needs)),
        ("FR-9  discoveryDb.TBL_REMINDERS (recurring payments)", "discoveryDb.db",
         any("discoveryDb" in n for n in needs)),
        ("FR-8  files/in_app_notification_model", "in_app_notification_model",
         any("in_app_notification" in n for n in needs)),
        ("FR-8  FCM token from com.google.android.gms.appid.xml", "com.google.android.gms.appid.xml",
         "com.google.android.gms.appid.xml" in needs),
        ("--    RealtimeSmsUploadDb (SMS upload evidence)", "RealtimeSmsUploadDb",
         any("RealtimeSms" in n for n in needs)),
        ("--    pai_signal / pai_push_signal (signal events)", "pai_signal",
         any("pai_signal" in n for n in needs)),
        ("--    AppLocale.db", "AppLocale.db", any("AppLocale" in n for n in needs)),
        ("--    files/AppEventsLogger.persistedevents", "AppEventsLogger.persistedevents",
         any("AppEvents" in n for n in needs)),
        ("--    files/datastore/*.preferences_pb", "datastore",
         any("datastore" in n for n in needs)),
    ]
    # A parser with `needs = ()` discovers its own files, so the `needs` tuple alone is
    # not evidence either way -- also check whether the artifact was actually READ.
    OUT_OF_SCOPE = {"AppLocale.db": "UI translation strings, not evidence"}
    for label, artifact, has in checks:
        members = [f for f in all_files if artifact in f]
        if not members:
            print(f"  [n/a ] {label:<52} artifact absent")
            continue
        was_read = any(f in read for f in members)
        if artifact in OUT_OF_SCOPE:
            print(f"  [n/a ] {label:<52} deliberately out of scope "
                  f"({OUT_OF_SCOPE[artifact]})")
        elif has or was_read:
            print(f"  [ok  ] {label:<52} "
                  f"{'parser registered' if has else 'read by a self-discovering parser'}")
        else:
            print(f"  [FAIL] {label:<52} NO PARSER AND NOT READ")

    section("7. DEAD MODEL CLASSES  (defined but never emitted)")
    from paytmforensics.core import models as M
    import dataclasses
    con = sqlite3.connect(os.path.join(out, "case.db"))
    emitted = {r[0] for r in con.execute("SELECT DISTINCT domain FROM records")}
    con.close()
    for name in dir(M):
        o = getattr(M, name)
        if dataclasses.is_dataclass(o) and isinstance(o, type) and issubclass(o, M.Record) and o is not M.Record:
            try:
                dom = o().domain
            except Exception:
                continue
            if dom not in emitted:
                print(f"  [FAIL] {name:<20} domain='{dom}' defined in models.py but NEVER emitted")

    section("8. CARVING SCOPE")
    from paytmforensics.carving import carve_runner
    from paytmforensics.core.artifact import discover as _disc, by_logical as _byl
    dbs = [f for f in all_files if f.startswith("databases/")
           and not f.endswith(("-wal", "-shm", "-journal"))]
    targets = carve_runner._targets(_byl(_disc(ext)))
    tnames = {os.path.basename(a.rel_path) for a in targets}
    print(f"  SQLite databases in extraction : {len(dbs)}")
    print(f"  databases the carver targets   : {len(targets)}")
    missed = [d for d in dbs if os.path.basename(d) not in tnames]
    print(f"  NEVER carved                   : {len(missed)}")
    for d in sorted(missed):
        print(f"      {d}")
    wal = [f for f in all_files if f.endswith("-wal")]
    carved_wals = [f for f in wal if f in carved_files]
    print(f"\n  -wal files (each holds old page images = prime carving material): {len(wal)}")
    print(f"  -wal files the carver opened   : {len(carved_wals)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
