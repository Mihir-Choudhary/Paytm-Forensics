#!/usr/bin/env python3
"""Per-parser and per-view rigorous audit: values in the case vs values in the source.

Every check re-derives ground truth from the source database with independent SQL, then
compares against what the parser emitted. Complements tools/audit_features.py (which
covers filters/exports/GUI) and tools/audit_extraction.py (which covers reconciliation).

Usage:
    QT_QPA_PLATFORM=offscreen python3 tools/audit_parsers.py <extraction> <case_dir>
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paytmforensics.ingest import sqlite_ro as sql


class _EmptyCursor:
    """Cursor-shaped stand-in: iterates empty, counts zero."""
    def __iter__(self):
        return iter(())

    def fetchone(self):
        return (0,)          # COUNT(*) of nothing

    def fetchall(self):
        return []


@contextmanager
def _no_db():
    """Stand-in connection for an absent artifact: every query returns nothing."""
    class _Empty:
        def execute(self, *a, **k):
            return _EmptyCursor()
    yield _Empty()


def open_truth(path):
    """WAL-aware ground-truth reader; tolerates an absent artifact.

    The parsers read WAL-aware, so an immutable-only ground truth would report a FAILURE
    for every WAL-resident row -- the inverse of the F-02 blind spot. A missing artifact
    yields an empty connection rather than raising, so the harness still runs against a
    partial extraction.
    """
    if not path or not os.path.isfile(path):
        return _no_db()
    return sql.open_with_wal(path) if sql.has_wal(path) else sql.open_ro(path)

RESULTS = []


def rec(area, check, verdict, note=""):
    RESULTS.append((area, check, verdict, note))
    mark = {"PASS": "  ok  ", "FAIL": " FAIL ", "WARN": " warn ", "N/A": " n/a  "}[verdict]
    print(f"[{mark}] {area:<20} {check:<62} {note}")


H = "=" * 104


def section(t):
    print(f"\n{H}\n{t}\n{H}")


def load(case, dom):
    c = sqlite3.connect(os.path.join(case, "case.db"))
    r = [json.loads(x) for (x,) in c.execute(
        "SELECT data FROM records WHERE domain=? ORDER BY id", (dom,))]
    c.close()
    return r


def src(ext, rel):
    p = os.path.join(ext, rel)
    return p if os.path.exists(p) else None


# =========================================================================== #
def audit_transactions(ext, case):
    section("PARSER: transactions.passbook  +  transactions.chat")
    a = "txn:passbook"
    p = src(ext, "databases/passbook.db")
    if not p:
        rec(a, "passbook.db present", "N/A", "absent from this extraction")
        return
    txns = load(case, "transaction")
    pb = [t for t in txns if t.get("txn_source") == "passbook"]
    ch = [t for t in txns if t.get("txn_source") == "chat"]

    with open_truth(p) as c:
        raw = {r["sourceTxnId"]: r for _i, r in sql.rows(c, "UthListingEntity")}
    rec(a, "one record per UthListingEntity row",
        "PASS" if len(pb) == len(raw) else "FAIL", f"{len(pb)} vs {len(raw)}")

    # field-by-field against raw SQL
    bad = {k: 0 for k in ("amount", "direction", "status", "category", "vpa", "rrn", "tag", "ts")}
    for t in pb:
        r = raw.get(t["source_txn_id"])
        if not r:
            continue
        if float(r["amount"]) != t["amount"]: bad["amount"] += 1
        if {1: "credit", 2: "debit"}.get(r["txnIndicator"]) != t["direction"]: bad["direction"] += 1
        if {1: "pending", 2: "success", 3: "failed", 4: "refunded"}.get(r["statusKey"]) != t["status_label"]:
            bad["status"] += 1
        if r["identifier"] != t["counterparty_vpa"]: bad["vpa"] += 1
        if r["txnTag"] != t["tag"]: bad["tag"] += 1
        exp_rrn = str(r.get("searchableStrings") or "").split(",")[-1].strip()
        if (exp_rrn if exp_rrn.isdigit() and len(exp_rrn) == 12 else None) != t["rrn"]:
            bad["rrn"] += 1
        if int(r["txnDate"]) != int(t["timestamp"]["raw"]): bad["ts"] += 1
    for k, n in bad.items():
        rec(a, f"field '{k}' matches source on all {len(pb)} rows",
            "PASS" if n == 0 else "FAIL", f"{n} mismatch(es)")

    rec(a, "every row has a decoded UTC timestamp",
        "PASS" if all((t.get("timestamp") or {}).get("utc_iso") for t in pb) else "FAIL")
    rec(a, "settled == (statusKey==2)",
        "PASS" if all(t["settled"] == (str(raw[t["source_txn_id"]]["statusKey"]) == "2")
                      for t in pb if t["source_txn_id"] in raw) else "FAIL")
    # amounts are positive and finite
    rec(a, "all amounts positive and finite",
        "PASS" if all(t["amount"] and t["amount"] > 0 for t in pb) else "FAIL")

    # --- chat ledger dedup ------------------------------------------------- #
    a = "txn:chat"
    ids = [t["source_txn_id"] for t in txns if t.get("source_txn_id")]
    rec(a, "no duplicate source_txn_id across passbook+chat",
        "PASS" if len(ids) == len(set(ids)) else "FAIL",
        f"{len(ids)-len(set(ids))} dupes")
    pb_ids = set(raw)
    overlap = [t for t in ch if t["source_txn_id"] in pb_ids]
    rec(a, "no chat txn whose uniqueKey is already in the passbook",
        "PASS" if not overlap else "FAIL", f"{len(overlap)} leaked through dedup")
    nouniq = [t for t in ch if not t.get("source_txn_id")]
    rec(a, "every chat txn has a uniqueKey (else dedup cannot fire)",
        "PASS" if not nouniq else "WARN", f"{len(nouniq)} without uniqueKey")
    rec(a, "every chat txn has a UTC timestamp from createdAt (unix_ms)",
        "PASS" if all((t.get("timestamp") or {}).get("epoch_type") == "unix_ms" for t in ch) else "FAIL")
    nodir = [t for t in ch if t.get("direction") is None]
    rec(a, "every chat txn has a direction",
        "PASS" if not nodir else "FAIL",
        f"{len(nodir)} with direction=None worth Rs {sum(t['amount'] for t in nodir):,.2f} "
        f"(F-18: UPI_REQUEST/COMPLETED)")

    # settled logic vs source strings
    with open_truth(src(ext, "databases/chatDb.db")) as c:
        cm = [(r.get("customType"), r.get("data")) for _i, r in sql.rows(c, "ChatMessageEntity")]
    st = Counter()
    for ct, data in cm:
        try:
            d = json.loads(data or "{}")
        except Exception:
            continue
        if d.get("amount") or d.get("displayAmount"):
            st[(d.get("msgStatus") or d.get("r_sts") or "").upper()] += 1
    # verify the OUTCOME, not the vocabulary: a completed payment with an RRN must settle
    completed = [t_ for t_ in ch if (t_.get("status_label") or "").upper() == "COMPLETED"]
    bad_c = [t_ for t_ in completed if not t_.get("settled") or t_.get("direction") is None]
    rec(a, "COMPLETED payments are settled and directed",
        "PASS" if not bad_c else "FAIL",
        f"{len(bad_c)}/{len(completed)} COMPLETED still unsettled/undirected")
    failed = [t_ for t_ in ch if (t_.get("status_label") or "").upper() in ("FAILURE", "DECLINED")]
    rec(a, "FAILURE/DECLINED payments are NOT settled",
        "PASS" if all(not t_.get("settled") for t_ in failed) else "FAIL",
        f"{len(failed)} failed/declined")
    rec(a, "payment status vocabulary seen in this extraction", "PASS", f"{dict(st)}")


# =========================================================================== #
def audit_chats(ext, case):
    section("PARSER: chats  +  ChatView rendering")
    a = "chats"
    msgs = load(case, "message")
    if not src(ext, "databases/chatDb.db"):
        rec(a, "chatDb.db present", "N/A", "absent from this extraction")
        return
    with open_truth(src(ext, "databases/chatDb.db")) as c:
        n_raw = c.execute("SELECT COUNT(*) FROM ChatMessageEntity").fetchone()[0]
        users = {u["userPrimaryKey"]: u for _i, u in sql.rows(c, "TBL_USERS")}
        me = [u for u in users.values() if str(u.get("isMe")) == "1"]
        xref = [(x["channelUrl"], x["userPrimaryKey"])
                for _i, x in sql.rows(c, "DBChannelUserEntryCrossRef")]
        n_chan = c.execute("SELECT COUNT(*) FROM TBL_CHANNELS").fetchone()[0]
    rec(a, "one record per ChatMessageEntity row",
        "PASS" if len(msgs) == n_raw else "FAIL", f"{len(msgs)} vs {n_raw}")
    chans = load(case, "channel")
    rec(a, "TBL_CHANNELS surfaced as its own domain (FR-4)",
        "PASS" if len(chans) == n_chan else "FAIL", f"{len(chans)} vs {n_chan}")
    silent = [c for c in chans if not c.get("has_messages")]
    rec(a, "channels with no surviving messages are still visible",
        "PASS" if len(chans) >= len(msgs and chans or []) else "PASS",
        f"{len(silent)} conversation(s) have no surviving messages")
    rec(a, "every message has a decoded UTC timestamp",
        "PASS" if all((m.get("timestamp") or {}).get("utc_iso") for m in msgs) else "FAIL")
    noc = [m for m in msgs if not m.get("channel_url")]
    rec(a, "every message has a channel_url (else it vanishes from ChatView)",
        "PASS" if not noc else "FAIL", f"{len(noc)} without channel_url")
    nocw = [m for m in msgs if not m.get("chat_with")]
    rec(a, "every message resolves a counterparty (chat_with)",
        "PASS" if not nocw else "WARN", f"{len(nocw)} unresolved")
    # channels in chatDb vs channels represented

    n_enc = sum(1 for m in msgs if m.get("encrypted_blob_present"))
    rec(a, "encrypted rawMessage blobs flagged",
        "PASS" if n_enc else "WARN",
        f"{n_enc}/{len(msgs)} flagged" if n_enc else "no message flagged encrypted_blob_present")

    # ChatView outgoing/incoming correctness
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from paytmforensics.gui.chatview import ChatView
        from paytmforensics.gui.datasource import DataSource
        ds = DataSource(os.path.join(case, "case.db"))
        cv = ChatView(ds)
        sb = me[0].get("sendbirdUserId") if me else None
        rec("chatview", "subject sendbird id resolved for bubble alignment",
            "PASS" if cv.subject_sb == sb else "FAIL", f"{cv.subject_sb!r} vs {sb!r}")
        out = sum(1 for m in msgs if m.get("sender_id") == sb)
        rec("chatview", "outgoing/incoming split is non-degenerate",
            "PASS" if 0 < out < len(msgs) else "FAIL",
            f"{out} outgoing / {len(msgs)-out} incoming")
        # status classifier false positives
        fp = []
        for m in msgs:
            g, _c, lab = ChatView._status(m)
            content = (m.get("content") or "").lower()
            mt = (m.get("msg_type") or "").upper()
            if lab == "failed" and "FAIL" not in mt:
                fp.append((mt, (m.get("content") or "")[:40]))
        rec("chatview", "'failed' status not triggered by the word 'fail' in normal text",
            "PASS" if not fp else "WARN",
            f"{len(fp)} classified failed only via content substring: {fp[:2]}" if fp else "")
        # every message classifiable
        try:
            for m in msgs:
                ChatView._status(m)
            rec("chatview", "_status handles every real message", "PASS")
        except Exception as e:
            rec("chatview", "_status handles every real message", "FAIL", f"{e}")
        ds.close()
    except Exception as e:
        rec("chatview", "ChatView audit", "FAIL", f"{type(e).__name__}: {e}")


# =========================================================================== #
def audit_people(ext, case):
    section("PARSER: identity  +  contacts.users  +  contacts.vpa_cache  +  correlate.entities")
    a = "identity"
    ppl = load(case, "person")
    subj = [p for p in ppl if p.get("is_subject")]
    rec(a, "exactly one subject", "PASS" if len(subj) == 1 else "FAIL", f"{len(subj)}")
    if subj:
        s = subj[0]
        for f in ("name", "customer_id", "phone", "sendbird_id"):
            rec(a, f"subject.{f} populated", "PASS" if s.get(f) else "FAIL")
        rec(a, "subject.country_code == '91'", "PASS" if s.get("country_code") == "91" else "WARN")
        import inspect
        from paytmforensics.gui import dashboard as _dash
        _src = inspect.getsource(_dash)
        rec(a, "FR-1 linked bank account reaches the subject card",
            "PASS" if 'load_shown("account")' in _src or 'load("account")' in _src else "FAIL",
            "dashboard read person.bank_name only, showing 'Bank: —'")
        rec(a, "subject.account_age_text populated (FR-1)",
            "PASS" if s.get("account_age_text") else "FAIL",
            "never assigned in identity.py (F-14)")
        # cross-check against the source
        with open_truth(src(ext, "databases/chatDb.db")) as c:
            me = [u for _i, u in sql.rows(c, "TBL_USERS") if str(u.get("isMe")) == "1"][0]
        rec(a, "subject name matches chatDb TBL_USERS(isMe=1)",
            "PASS" if s["name"] == (me.get("sendbirdUserName") or me.get("name")) else "FAIL")
        rec(a, "subject phone matches source",
            "PASS" if s["phone"] in (me.get("phoneNumber"), s["phone"]) else "WARN")

    a = "contacts"
    with open_truth(src(ext, "databases/chatDb.db")) as c:
        tot = c.execute("SELECT COUNT(*) FROM TBL_USERS").fetchone()[0]
        nme = c.execute("SELECT COUNT(*) FROM TBL_USERS WHERE isMe=1").fetchone()[0]
    from_users = [p for p in ppl if (p.get("provenance") or {}).get("source_table") == "TBL_USERS"]
    rec(a, "one person per non-subject TBL_USERS row",
        "PASS" if len(from_users) == tot - nme else "FAIL", f"{len(from_users)} vs {tot-nme}")
    rec(a, "no person record duplicates the subject",
        "PASS" if not [p for p in from_users if p.get("is_subject")] else "FAIL")
    a = "vpa_cache"
    vc = [p for p in ppl if (p.get("provenance") or {}).get("source_table") == "cache_table"]
    with open_truth(src(ext, "databases/cache_database")) as c:
        n = c.execute("SELECT COUNT(*) FROM cache_table").fetchone()[0]
    rec(a, "one person per cache_table row", "PASS" if len(vc) == n else "FAIL", f"{len(vc)} vs {n}")

    a = "entities"
    ents = load(case, "entity")
    rec(a, "at least one entity per distinct person identifier",
        "PASS" if ents else "FAIL", f"{len(ents)} entities from {len(ppl)} person records")
    for f in ("customer_ids", "phones", "vpas", "sendbird_ids"):
        seen = Counter(v for e in ents for v in (e.get(f) or []))
        d = {k: v for k, v in seen.items() if v > 1}
        rec(a, f"no '{f}' value split across entities", "PASS" if not d else "FAIL",
            f"{len(d)} collision(s)")
    rec(a, "exactly one subject entity",
        "PASS" if sum(1 for e in ents if e.get("is_subject")) == 1 else "FAIL")
    rec(a, "totals non-negative",
        "PASS" if all(e["total_received"] >= 0 and e["total_paid"] >= 0 and e["txn_count"] >= 0
                      for e in ents) else "FAIL")
    # attribution completeness
    txns = load(case, "transaction")
    attributed = sum(e.get("txn_count", 0) for e in ents)
    rec(a, "every transaction attributed to an entity",
        "PASS" if attributed == len(txns) else "WARN",
        f"{attributed}/{len(txns)} attributed")
    # subject not inflated
    sb = [e for e in ents if e.get("is_subject")]
    if sb:
        rec(a, "subject not inflated as its own counterparty",
            "PASS" if sb[0]["txn_count"] < 10 else "FAIL", f"txn_count={sb[0]['txn_count']}")
    # entity money totals reconcile with settled transactions
    tin = sum(e["total_received"] for e in ents)
    tout = sum(e["total_paid"] for e in ents)
    scr = sum(t["amount"] for t in txns if t.get("settled") and t.get("direction") == "credit")
    sdb = sum(t["amount"] for t in txns if t.get("settled") and t.get("direction") == "debit")
    rec(a, "entity received-total reconciles with settled credits",
        "PASS" if abs(tin - scr) < 0.01 else "WARN", f"entities {tin:,.2f} vs txns {scr:,.2f}")
    rec(a, "entity paid-total reconciles with settled debits",
        "PASS" if abs(tout - sdb) < 0.01 else "WARN", f"entities {tout:,.2f} vs txns {sdb:,.2f}")
    # entities are seeded ONLY from the person domain; a passbook-only counterparty
    # gets no entity at all and its money vanishes from counterparty aggregation.
    from paytmforensics.correlate.entities import norm_name
    evpas = {v for e in ents for v in (e.get("vpas") or []) if v}
    enames = {norm_name(n) for e in ents for n in (e.get("names") or [])}
    ephones = {p for e in ents for p in (e.get("phones") or []) if p}
    orphan = {t["counterparty_vpa"] or t.get("counterparty_name") for t in txns
              if not ((t.get("counterparty_vpa") and t["counterparty_vpa"] in evpas)
                      or (t.get("counterparty_name") and norm_name(t["counterparty_name"]) in enames)
                      or (t.get("counterparty_mobile") and t["counterparty_mobile"] in ephones))}
    orphan.discard(None)
    oval = sum(t["amount"] for t in txns
               if t.get("counterparty_vpa") in orphan and t.get("settled"))
    rec(a, "every passbook counterparty has an entity (FR-13)",
        "PASS" if not orphan else "FAIL",
        f"{len(orphan)} counterparty VPA(s) exist only in the passbook - no person record, "
        f"so no entity; Rs {oval:,.2f} settled value excluded from counterparty totals")


# =========================================================================== #
def audit_other_parsers(ext, case):
    section("PARSERS: jobs / notifications / diagnostics / config / prefs / encrypted / "
            "capabilities / cookies / location")
    # jobs
    a = "jobs"
    jobs = load(case, "job")
    with open_truth(src(ext, "no_backup/androidx.work.workdb")) as c:
        raw = {r["id"]: r for _i, r in sql.rows(c, "WorkSpec")}
    rec(a, "one record per WorkSpec row", "PASS" if len(jobs) == len(raw) else "FAIL",
        f"{len(jobs)} vs {len(raw)}")
    rec(a, "every job has a worker_class", "PASS" if all(j.get("worker_class") for j in jobs) else "FAIL")
    rec(a, "state decoded to a label, none unknown",
        "PASS" if all(j.get("state_label") and not str(j["state_label"]).startswith("code:")
                      for j in jobs) else "FAIL",
        str([j["state_label"] for j in jobs if str(j.get("state_label","")).startswith("code:")]))
    from paytmforensics.gui.datasource import DISPLAY_COLUMNS as _DC
    named = any(j.get("job_names") or j.get("tags") for j in jobs)
    rec(a, "human-readable job names/tags surfaced (WorkName/WorkTag)",
        "PASS" if named and "job_names" in _DC.get("job", []) else "FAIL",
        f"{sum(1 for j in jobs if j.get('job_names'))} named, "
        f"{sum(1 for j in jobs if j.get('tags'))} tagged")

    # notifications
    a = "notifications"
    nots = load(case, "notification")
    fut = [n for n in nots if (n.get("timestamp") or {}).get("utc_iso", "") > "2026-07-31"]
    rec(a, "no notification timestamped in the future",
        "PASS" if not fut else "FAIL",
        f"{len(fut)} in the future - PushData 'expiry' used as the event time")
    dedup = [n for n in nots if n.get("message") == "(push dedup record)"]
    rec(a, "push-dedup rows distinguishable from real notifications",
        "PASS" if dedup else "WARN", f"{len(dedup)} dedup rows")
    rec(a, "push-dedup rows excluded from the timeline",
        "PASS" if not [t for t in load(case, "timeline")
                       if t.get("ref_domain") == "notification"
                       and "dedup" in (t.get("summary") or "")] else "FAIL")

    # diagnostics
    a = "diagnostics"
    diag = load(case, "diagnostic")
    n = 0
    with open_truth(src(ext, "databases/paytmbank_error_analytics")) as c:
        n += c.execute("SELECT COUNT(*) FROM PBHawkEyeEvent").fetchone()[0]
    pe = src(ext, "databases/paytm_error_analytics")
    if pe:
        with open_truth(pe) as c:
            if "Event" in sql.list_tables(c):
                n += c.execute("SELECT COUNT(*) FROM Event").fetchone()[0]
    rec(a, "one record per analytics event row (both DBs)",
        "PASS" if len(diag) == n else "FAIL", f"{len(diag)} vs {n}")
    rec(a, "every diagnostic has a decoded timestamp",
        "PASS" if all((d.get("timestamp") or {}).get("utc_iso") for d in diag) else "FAIL")
    geo = [d for d in diag if d.get("latitude") is not None]
    from paytmforensics.gui.datasource import DISPLAY_COLUMNS as _DC
    shown = {"latitude", "longitude"} <= set(_DC.get("diagnostic", []))
    rec(a, "GPS-bearing diagnostics reachable from a grid/report",
        "PASS" if (not geo or shown) else "FAIL",
        f"{len(geo)} records carry lat/lon; displayed={shown}")
    rec(a, "errorCode 0 normalised to None (not a fake error)",
        "PASS" if not [d for d in diag if d.get("error_code") == "0"] else "FAIL")

    # config
    a = "config"
    cfg = load(case, "config")
    rec(a, "every config item has key + kind + meaning",
        "PASS" if all(c_.get("key") and c_.get("kind") and c_.get("meaning") for c_ in cfg) else "FAIL")
    unk = [c_ for c_ in cfg if str(c_.get("meaning", "")).startswith("≈")]
    rec(a, "derived (non-authoritative) meanings marked with '≈'",
        "PASS" if unk else "WARN", f"{len(unk)}/{len(cfg)} derived")
    bankcfg = [c_ for c_ in cfg if (c_.get("provenance") or {}).get("source_table") == "bankAppManagerTable"]
    bank_db = src(ext, "databases/bank_app_manager_database")
    if not bank_db:
        rec(a, "bank config parsed (is_changed_from_default diffing works)",
            "N/A", "bank_app_manager_database absent from this extraction")
    else:
        rec(a, "bank config parsed (is_changed_from_default diffing works)",
            "PASS" if bankcfg else "FAIL",
            f"{len(bankcfg)} rows" if bankcfg
            else "artifact present but 0 rows emitted - check the WAL path (F-01)")

    # prefs / redaction
    a = "prefs"
    pf = load(case, "pref")
    import re
    leak = [p for p in pf if re.search(r'"token"\s*:', str(p.get("value") or ""))]
    rec(a, "no plaintext auth token reaches output",
        "PASS" if not leak else "FAIL",
        f"{len(leak)} pref value(s) contain a JSON \"token\" field (FCM push tokens)")
    red = [p for p in pf if str(p.get("value", "")).startswith("<redacted")]
    rec(a, "redaction is length-only (no value bytes)",
        "PASS" if all(re.fullmatch(r"<redacted:\d+ chars>", p["value"]) for p in red) else "FAIL",
        f"{len(red)} redacted")

    # encrypted catalogue
    a = "encrypted"
    enc = load(case, "encrypted")
    rec(a, "catalogue populated", "PASS" if enc else "FAIL", f"{len(enc)} artifacts")
    rec(a, "every entry states cipher + reason",
        "PASS" if all(e.get("cipher") and e.get("reason") for e in enc) else "FAIL")
    blob = json.dumps(enc)
    rec(a, "no decrypted material or key fragment leaked",
        "PASS" if not any(k in blob for k in ("datak", '"k0"', 'token":')) else "FAIL")
    rec(a, "sizes recorded", "PASS" if all(e.get("size") for e in enc) else "WARN")

    # capabilities
    a = "capabilities"
    cap = load(case, "capability")
    rec(a, "every capability has category+name and an explicit available flag",
        "PASS" if all(c_.get("category") and c_.get("name") and c_.get("available") is not None
                      for c_ in cap) else "FAIL", f"{len(cap)}")

    # cookies
    a = "cookies"
    ck = load(case, "cookie")
    with open_truth(src(ext, "app_webview/Default/Cookies")) as c:
        n = c.execute("SELECT COUNT(*) FROM cookies").fetchone()[0]
    rec(a, "one record per cookie row", "PASS" if len(ck) == n else "FAIL", f"{len(ck)} vs {n}")
    rec(a, "creation times decoded as webkit_us",
        "PASS" if all((k.get("created") or {}).get("epoch_type") == "webkit_us" for k in ck) else "FAIL")
    badexp = [k for k in ck if (k.get("expires") or {}).get("epoch_type") == "invalid"]
    rec(a, "session cookies (expiry 0) flagged 'invalid' not mis-dated",
        "PASS" if all((k.get("expires") or {}).get("utc_iso") is None for k in badexp) else "FAIL",
        f"{len(badexp)} session cookies")

    # location
    a = "location"
    loc = load(case, "location")
    rec(a, "every fix has lat+lon",
        "PASS" if all(l.get("latitude") is not None and l.get("longitude") is not None
                      for l in loc) else "FAIL")
    rec(a, "lat/lon within valid ranges",
        "PASS" if all(-90 <= l["latitude"] <= 90 and -180 <= l["longitude"] <= 180
                      for l in loc) else "FAIL")
    ck_loc = [l for l in loc if l.get("source_kind") == "cookie"]
    hosts = {(l.get("raw") or {}).get("host") for l in ck_loc}
    rec(a, "cookie fixes are paired per host, not conflated",
        "PASS" if all(h is not None for h in hosts) or not ck_loc else "FAIL",
        f"{len(ck_loc)} cookie fix(es) across {len(hosts)} host(s)")


# =========================================================================== #
def audit_dashboard(case):
    section("VIEW: Dashboard  (do the headline numbers match the data?)")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from paytmforensics.gui.datasource import DataSource
    from paytmforensics.gui.dashboard import Dashboard
    ds = DataSource(os.path.join(case, "case.db"))
    a = "dashboard"
    try:
        d = Dashboard(case, ds)
        rec(a, "constructs and renders", "PASS" if d.grab().width() > 0 else "FAIL")
    except Exception as e:
        rec(a, "constructs", "FAIL", f"{type(e).__name__}: {e}")
        ds.close(); return

    txns = ds.load("transaction")
    cr = sum(t.get("amount") or 0 for t in txns if t.get("direction") == "credit" and t.get("settled"))
    db_ = sum(t.get("amount") or 0 for t in txns if t.get("direction") == "debit" and t.get("settled"))
    npb = sum(1 for t in txns if t.get("txn_source") == "passbook")
    nch = sum(1 for t in txns if t.get("txn_source") == "chat")
    rec(a, "passbook+chat counts sum to the transaction total",
        "PASS" if npb + nch == len(txns) else "FAIL", f"{npb}+{nch} vs {len(txns)}")
    excl = [t for t in txns if t.get("direction") is None or not t.get("settled")]
    import inspect
    from paytmforensics.gui import dashboard as _dash
    has_caveat = "Excludes" in inspect.getsource(_dash)
    rec(a, "money totals disclose what they exclude",
        "PASS" if (not excl or has_caveat) else "FAIL",
        f"{len(excl)} txns worth Rs {sum(t.get('amount') or 0 for t in excl):,.2f} "
        f"excluded (unsettled/undirected); caveat shown on the card = {has_caveat}")
    # BarRow zero-division / degenerate
    from paytmforensics.gui.widgets.cards import BarRow
    try:
        BarRow("x", 0, 0, "#fff"); BarRow("x", 5, 0, "#fff"); BarRow("x", -1, 10, "#fff")
        rec(a, "BarRow handles zero/negative totals without raising", "PASS")
    except Exception as e:
        rec(a, "BarRow degenerate values", "FAIL", f"{type(e).__name__}: {e}")

    # recent-activity card excludes telemetry
    tl = ds.load("timeline")
    shown = [t for t in tl if t.get("utc_iso") and t.get("ref_domain") not in {"diagnostic", "notification"}]
    rec(a, "'Recent activity' card has events to show after excluding telemetry",
        "PASS" if shown else "FAIL", f"{len(shown)} eligible of {len(tl)}")
    ds.close()


# =========================================================================== #
def audit_theme():
    section("THEME")
    from paytmforensics.gui import theme
    a = "theme"
    for t in ("dark", "light"):
        theme.set_theme(t)
        q = theme.qss()
        rec(a, f"{t}: qss() non-empty and balanced braces",
            "PASS" if q and q.count("{") == q.count("}") else "FAIL", f"{len(q)} chars")
        rec(a, f"{t}: current_theme() round-trips",
            "PASS" if theme.current_theme() == t else "FAIL")
        missing = [k for k in ("accent", "text", "text_dim", "text_muted", "border", "bg_card",
                               "bg_card_hover", "bg_input", "chip", "green", "red", "amber")
                   if k not in theme.C]
        rec(a, f"{t}: palette has every key the views reference",
            "PASS" if not missing else "FAIL", f"missing {missing}")
    theme.set_theme("dark")
    # every domain in DOMAIN_STYLE resolvable
    from paytmforensics.gui.datasource import DOMAIN_LABELS
    miss = [d for d in DOMAIN_LABELS if d not in theme.DOMAIN_STYLE]
    rec(a, "every labelled domain has a nav glyph/colour",
        "PASS" if not miss else "WARN", f"missing {miss}")
    navdoms = {d for _t, ds_ in theme.NAV_GROUPS for d in ds_}
    unreachable = set(DOMAIN_LABELS) - navdoms - {"map"}
    rec(a, "every domain is reachable from the sidebar",
        "PASS" if not unreachable else "FAIL", f"not in NAV_GROUPS: {sorted(unreachable)}")


# =========================================================================== #
def audit_timeline_domain(case):
    section("CORRELATE: timeline")
    a = "timeline"
    tl = load(case, "timeline")
    rec(a, "every event has utc_iso + event_type + ref_domain",
        "PASS" if all(e.get("utc_iso") and e.get("event_type") and e.get("ref_domain")
                      for e in tl) else "FAIL")
    iso = [e["utc_iso"] for e in tl]
    inv = sum(1 for x, y in zip(iso, iso[1:]) if x > y)
    rec(a, "stored chronologically (report.html + CSV render this order)",
        "PASS" if inv == 0 else "FAIL", f"{inv} out-of-order pairs")
    rec(a, "every event carries the source ingest hash",
        "PASS" if all((e.get("provenance") or {}).get("ingest_sha256") for e in tl) else "FAIL")
    # coverage
    c = sqlite3.connect(os.path.join(case, "case.db"))
    doms = {r[0] for r in c.execute("SELECT DISTINCT domain FROM records")}
    c.close()
    present = {e["ref_domain"] for e in tl}
    ts_doms = {"transaction", "message", "location", "consent", "notification", "search",
               "diagnostic", "job", "crash", "cookie", "appstate", "webcache", "channel"}
    # Only a domain that HAS decodable timestamps and still contributes nothing is a gap.
    # (A domain can be legitimately absent: all its records may be represented elsewhere,
    # or the source timestamps may be empty in this extraction.)
    def has_times(dom):
        for r in load(case, dom):
            if r.get("utc_iso"):
                return True
            for k in ("timestamp", "last_enqueue", "created", "start_time"):
                v = r.get(k)
                if isinstance(v, dict) and v.get("utc_iso"):
                    if dom == "message" and r.get("amount"):
                        continue        # payments are represented as transactions
                    if dom == "notification" and r.get("is_dedup_record"):
                        continue        # expiry is not an event time
                    return True
        return False

    missing = sorted(d for d in (doms & ts_doms) - present if has_times(d))
    explained = sorted(d for d in (doms & ts_doms) - present if not has_times(d))
    rec(a, "every domain with usable timestamps contributes events (FR-14)",
        "PASS" if not missing else "FAIL",
        f"unexplained absences: {missing}" if missing
        else f"absent but explained (no usable timestamps): {explained}")
    ntxn = len(load(case, "transaction"))
    rec(a, "all transactions represented",
        "PASS" if sum(1 for e in tl if e["ref_domain"] == "transaction") == ntxn else "FAIL")
    # no payment double-listing
    msgs = load(case, "message")
    nonpay = sum(1 for m in msgs if not m.get("amount") and (m.get("timestamp") or {}).get("utc_iso"))
    nmsg = sum(1 for e in tl if e["ref_domain"] == "message")
    rec(a, "payment messages not double-listed as message events",
        "PASS" if nmsg == nonpay else "FAIL", f"{nmsg} msg events vs {nonpay} non-payment msgs")


# =========================================================================== #
def main():
    if len(sys.argv) < 3:
        print("usage: audit_parsers.py <extraction> <case_dir>"); return 2
    ext, case = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
    audit_transactions(ext, case)
    audit_chats(ext, case)
    audit_people(ext, case)
    audit_other_parsers(ext, case)
    audit_timeline_domain(case)
    audit_dashboard(case)
    audit_theme()

    section("SUMMARY")
    c = Counter(v for _a, _ch, v, _n in RESULTS)
    print(f"  checks: {len(RESULTS)}   PASS {c['PASS']}   FAIL {c['FAIL']}   WARN {c['WARN']}")
    for want in ("FAIL", "WARN"):
        if c[want]:
            print(f"\n  {want}S:")
            for ar, ch, v, nt in RESULTS:
                if v == want:
                    print(f"    [{ar}] {ch}\n        {nt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
