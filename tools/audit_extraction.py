#!/usr/bin/env python3
"""Reconciliation harness for a real Paytm extraction.

Answers "is everything parsed, with correct values, and displayed correctly?" by
comparing the extraction's raw contents against what PaytmForensics emits.

Usage:
    python3 audit_extraction.py /path/to/net.one97.paytm [/path/to/case_out]

Sections:
  0  WAL exposure        - rows visible only in an uncheckpointed -wal (silently dropped)
  1  Parse errors        - parsers that raised and were swallowed into results[name] = -1
  2  Table reconciliation- every table in every DB: raw COUNT(*) vs records emitted
  3  Enum coverage       - distinct txnIndicator/statusKey/txnCategory/WorkSpec.state
  4  Timestamp census    - epoch_type distribution + min/max per domain
  5  searchableStrings   - RRN-is-last-token assumption miss rate
  6  Field coverage      - parsed fields that never reach the GUI grid / HTML report
  7  Timeline integrity  - chronological ordering + which domains are represented
  8  Entity resolution   - identifier collisions across entities (order-dependent merge)
  9  Secret hygiene      - unredacted token-shaped values in output
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paytmforensics.core.case import Case
from paytmforensics.core.artifact import discover
from paytmforensics.ingest import sqlite_ro as sql
from paytmforensics.gui.datasource import DISPLAY_COLUMNS
from paytmforensics.report.html import REPORT_DOMAINS

H = "=" * 78


def hdr(n, title):
    print(f"\n{H}\n{n}. {title}\n{H}")


def is_sqlite(path: str) -> bool:
    """Magic-byte sniff. Independent of artifact._kind_for, which only recognises
    KNOWN_DBS under databases/ and would miss a flat directory of database files."""
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


# --------------------------------------------------------------------------- #
def sec0_wal(root):
    """Quantify what an immutable-only read would hide.

    NOTE: since the WAL fix the parsers read WAL-aware, so this section measures the
    EXPOSURE (what a naive immutable read would miss), not what the tool now sees.
    Section 2 shows what the parser actually emitted.
    """
    hdr(0, "WAL EXPOSURE  (what an IMMUTABLE-ONLY read would miss; the parser now reads "
           "WAL-aware)")
    found = False
    total_hidden = 0
    for dp, _d, files in os.walk(root):
        for n in files:
            if not n.endswith("-wal"):
                continue
            wal = os.path.join(dp, n)
            db = wal[:-4]
            if not os.path.exists(db):
                continue
            found = True
            wsz = os.path.getsize(wal)
            # immutable=1 view (what the tool sees)
            immut, plain = {}, {}
            try:
                with sql.open_ro(db) as c:
                    for t in sql.list_tables(c):
                        try:
                            immut[t] = c.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
                        except sqlite3.DatabaseError:
                            immut[t] = None
            except sqlite3.DatabaseError:
                pass
            # WAL-aware view, on a COPY so the source is never touched.
            # The copy is always removed - leaving copied evidence in /tmp is a
            # chain-of-custody problem, not just untidiness.
            import shutil
            tmp = tempfile.mkdtemp(prefix="walchk_")
            try:
                for suf in ("", "-wal", "-shm"):
                    if os.path.exists(db + suf):
                        shutil.copy2(db + suf, os.path.join(tmp, os.path.basename(db) + suf))
                cp = os.path.join(tmp, os.path.basename(db))
                c2 = sqlite3.connect(f"file:{cp}?mode=ro", uri=True)
                for t in immut:
                    try:
                        plain[t] = c2.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
                    except sqlite3.DatabaseError:
                        plain[t] = None
                c2.close()
            except Exception as e:
                print(f"  ! {os.path.basename(db)}: WAL-aware read failed: {e}")
                continue
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            deltas = {t: (immut[t], plain[t]) for t in immut
                      if immut[t] != plain[t] and None not in (immut[t], plain[t])}
            status = "no delta" if not deltas else "*** WAL CHANGES STATE ***"
            print(f"  {os.path.basename(db):<38} -wal={wsz:>9,}B  {status}")
            for t, (i, p) in sorted(deltas.items()):
                print(f"       {t:<34} immutable {i:>7,}   with WAL {p:>7,}   delta {p-i:>+7,}")
                total_hidden += p - i
    if not found:
        print("  No -wal files in the extraction (all DBs checkpointed) -> not affected.")
    else:
        print(f"\n  NET ROWS AN IMMUTABLE-ONLY READ WOULD MISS: {total_hidden:,}")
        print("  (the parser reads these WAL-aware; see section 2 for what it emitted)")
    return total_hidden


# --------------------------------------------------------------------------- #
def sec1_parse_errors(out, results):
    hdr(1, "PARSE ERRORS  (swallowed by Case.parse_all -> results[name] = -1)")
    bad = {k: v for k, v in results.items() if v is not None and v < 0}
    if bad:
        for k in bad:
            print(f"  *** {k}: PARSER RAISED")
    else:
        print("  No parser raised.")
    log = os.path.join(out, "audit.log")
    if os.path.exists(log):
        for line in open(log, encoding="utf-8"):
            e = json.loads(line)
            if e.get("action") == "parse_error":
                print(f"  *** {e['detail'].get('parser')}: {e['detail'].get('error')}")
    print("\n  Records emitted per parser:")
    for k, v in sorted(results.items()):
        print(f"    {k:<34} {v:>8}")
    return bad


# --------------------------------------------------------------------------- #
#: domains built FROM other records - they re-carry the original provenance, so counting
#: them would double-count the source table.
DERIVED_DOMAINS = ("timeline", "entity", "carved")

#: tables read only as a join/lookup side-input; no records of their own is correct.
JOIN_ONLY = {("passbook.db", "UthInstrumentEntity")}


def sec2_tables(root, out):
    hdr(2, "TABLE RECONCILIATION  (raw rows vs records emitted per source table)")
    print(f"  (excludes derived domains {DERIVED_DOMAINS} which re-carry source provenance)\n")
    con = sqlite3.connect(os.path.join(out, "case.db"))
    ph = ",".join("?" * len(DERIVED_DOMAINS))
    emitted = defaultdict(Counter)
    for sf, st, d_, n in con.execute(
            f"SELECT source_file, source_table, domain, COUNT(*) FROM records "
            f"WHERE domain NOT IN ({ph}) GROUP BY 1,2,3", DERIVED_DOMAINS):
        emitted[(sf, st)][d_] = n
    con.close()

    unparsed, mismatched = [], []
    for art in discover(root):
        # NOTE: do NOT filter on art.kind. artifact._kind_for only returns "sqlite" for
        # names in KNOWN_DBS under databases/, but by_logical indexes every artifact and
        # BaseParser.available() checks that index - so the tool can parse a DB this
        # section would refuse to reconcile. Sniff the magic bytes instead, which is
        # correct for a full extraction tree AND for a flat directory of db files.
        if not is_sqlite(art.abs_path):
            continue
        try:
            with sql.open_ro(art.abs_path) as c:
                for t in sql.list_tables(c):
                    try:
                        raw = c.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
                    except sqlite3.DatabaseError:
                        raw = None
                    per = emitted.get((art.rel_path, t), Counter())
                    got = sum(per.values())
                    # per-domain max: one row may legitimately fan out to several domains
                    biggest = max(per.values()) if per else 0
                    flag = ""
                    if raw and got == 0:
                        if (os.path.basename(art.rel_path), t) in JOIN_ONLY:
                            flag = "  (join-only input - expected)"
                        else:
                            flag = "  <-- ROWS PRESENT, NOTHING EMITTED"
                            unparsed.append((art.rel_path, t, raw))
                    elif raw and biggest and biggest < raw:
                        flag = f"  <-- only {biggest}/{raw} rows became records"
                        mismatched.append((art.rel_path, t, raw, biggest))
                    dstr = " ".join(f"{k}={v}" for k, v in sorted(per.items()))
                    print(f"  {art.rel_path:<44} {t:<30} raw={str(raw):>8}  {dstr}{flag}")
        except sqlite3.DatabaseError as e:
            print(f"  ! {art.rel_path}: {e}")

    if unparsed:
        print(f"\n  TABLES WITH DATA BUT NO PARSER ({len(unparsed)}):")
        tot = 0
        for f, t, n in sorted(unparsed, key=lambda x: -x[2]):
            print(f"    {n:>9,} rows   {f} :: {t}")
            tot += n
        print(f"    {tot:>9,} rows TOTAL unparsed")
    if mismatched:
        print(f"\n  TABLES ONLY PARTIALLY EMITTED ({len(mismatched)}) - check filter logic:")
        for f, t, raw, got in sorted(mismatched, key=lambda x: x[3] - x[2]):
            print(f"    {got:>8,}/{raw:<8,} {f} :: {t}   ({raw-got:,} rows dropped)")
    return unparsed, mismatched


# --------------------------------------------------------------------------- #
def sec3_enums(root, out):
    hdr(3, "ENUM COVERAGE  (values landing on 'code:N' are undecoded)")
    from paytmforensics.enrich import enums
    checks = [
        ("passbook.db", "UthListingEntity", "txnIndicator", enums.TXN_INDICATOR, "direction"),
        ("passbook.db", "UthListingEntity", "statusKey", enums.STATUS_KEY, "status"),
        ("passbook.db", "UthListingEntity", "txnCategory", enums.TXN_CATEGORY, "category"),
    ]
    idx = {a.logical: a for a in discover(root)}
    for logical, table, col, tbl, label in checks:
        art = idx.get(logical)
        if not art:
            print(f"  {logical}: absent")
            continue
        try:
            with sql.open_ro(art.abs_path) as c:
                if table not in sql.list_tables(c):
                    continue
                rows = c.execute(
                    f"SELECT {col}, COUNT(*) FROM '{table}' GROUP BY 1 ORDER BY 2 DESC").fetchall()
        except sqlite3.DatabaseError:
            continue
        print(f"\n  {col} ({label}):")
        for v, n in rows:
            known = v is None or (isinstance(v, (int, float)) and int(v) in tbl)
            mark = "" if known else "   *** UNMAPPED -> renders as code:%s" % v
            print(f"    {str(v):>8} : {n:>7,} rows   -> {tbl.get(int(v)) if known and v is not None else 'code:%s' % v}{mark}")
        if col == "statusKey":
            unmapped_success_risk = [v for v, n in rows if v is not None and str(v) != "2"]
            print(f"    NOTE: settled=True only for statusKey==2; {sum(n for v,n in rows if str(v)!='2'):,} "
                  f"rows are settled=False and EXCLUDED from money totals (codes {unmapped_success_risk})")

    w = idx.get("androidx.work.workdb")
    if w:
        try:
            with sql.open_ro(w.abs_path) as c:
                if "WorkSpec" in sql.list_tables(c):
                    print("\n  WorkSpec.state:")
                    for v, n in c.execute("SELECT state, COUNT(*) FROM WorkSpec GROUP BY 1"):
                        print(f"    {str(v):>8} : {n:>7,} -> {enums.WORK_STATE.get(v, 'code:%s' % v)}")
        except sqlite3.DatabaseError:
            pass

    # ---- chat payment path: string matches, not enum tables ------------------ #
    # ChatLedgerParser decides `settled` and `direction` by exact string comparison.
    # Both are whole-domain-wrong failure modes if the real spellings differ.
    chat = idx.get("chatDb.db")
    if not chat:
        print("\n  chatDb.db absent - chat payment path not checked")
        return
    print("\n  --- chat payment path (transactions.chat) ---")
    try:
        with sql.open_ro(chat.abs_path) as c:
            tabs = sql.list_tables(c)
            if "ChatMessageEntity" in tabs:
                ctypes, statuses, no_uniq, with_amt = Counter(), Counter(), 0, 0
                for _rid, r in sql.rows(c, "ChatMessageEntity"):
                    ctypes[(r.get("customType") or "").upper()] += 1
                    try:
                        d = json.loads(r.get("data") or "{}")
                    except (ValueError, TypeError):
                        continue
                    if d.get("amount") or d.get("displayAmount"):
                        with_amt += 1
                        statuses[(d.get("msgStatus") or d.get("r_sts") or "").upper()] += 1
                        if not d.get("uniqueKey"):
                            no_uniq += 1
                print(f"  payment messages (have amount): {with_amt:,}")
                print("  customType values:")
                for v, n in ctypes.most_common():
                    print(f"    {v or '(empty)':<34} {n:>7,}")
                print("  msgStatus / r_sts on payment messages:")
                for v, n in statuses.most_common():
                    ok = (v == "SUCCESS")
                    print(f"    {v or '(empty)':<34} {n:>7,}"
                          f"{'' if ok else '   *** != SUCCESS -> settled=False, EXCLUDED from money totals'}")
                if no_uniq:
                    print(f"  *** {no_uniq:,} payment messages have NO uniqueKey -> dedup vs "
                          f"passbook cannot fire -> risk of double-counting (O-7)")

            # direction depends entirely on resolving the subject's sendbird id
            if "TBL_USERS" in tabs:
                me = [u for _r, u in sql.rows(c, "TBL_USERS") if str(u.get("isMe")) == "1"]
                print(f"\n  TBL_USERS rows with isMe=1: {len(me)}"
                      f"{'   *** expected exactly 1' if len(me) != 1 else ''}")
                if len(me) != 1:
                    print("      -> subject_sb is unresolved/ambiguous; ChatLedgerParser falls "
                          "through to direction='credit' for EVERY chat payment")
                else:
                    sb = me[0].get("sendbirdUserId")
                    senders = Counter(r.get("senderId")
                                      for _x, r in sql.rows(c, "ChatMessageEntity"))
                    all_sb = {u.get("sendbirdUserId")
                              for _x, u in sql.rows(c, "TBL_USERS") if u.get("sendbirdUserId")}
                    print(f"  subject sendbirdUserId    : {sb!r}")
                    n = senders.get(sb, 0)
                    if n:
                        print(f"  appears as senderId on {n:,}/{sum(senders.values()):,} messages"
                              f"  -> direction logic fires correctly")
                    elif all_sb & set(senders):
                        # ids DO join, the subject just sent nothing in this data set
                        print(f"  subject sent 0 messages, but other TBL_USERS ids DO match "
                              f"senderId -> id formats join; direction defaults to 'credit' "
                              f"for all {sum(senders.values()):,} messages, which is correct "
                              f"only if the subject genuinely never sent one. VERIFY manually.")
                    else:
                        print("  *** NO TBL_USERS.sendbirdUserId matches ANY "
                              "ChatMessageEntity.senderId -> the two id spaces do not join")
                        print("      -> every chat transaction is labelled 'credit' "
                              "(systematic money-in inflation, no error raised)")
                        print(f"      sample senderIds : {list(senders)[:4]}")
                        print(f"      sample user ids  : {list(all_sb)[:4]}")
    except sqlite3.DatabaseError as e:
        print(f"  ! chatDb.db: {e}")


# --------------------------------------------------------------------------- #
def sec4_timestamps(out):
    hdr(4, "TIMESTAMP CENSUS  (epoch_type per domain; 'unknown'/'invalid' = misdetected)")
    con = sqlite3.connect(os.path.join(out, "case.db"))
    per = defaultdict(Counter)
    span = defaultdict(list)
    for d_, data in con.execute("SELECT domain, data FROM records"):
        r = json.loads(data)
        for key in ("timestamp", "last_enqueue", "created", "expires", "start_time"):
            ts = r.get(key)
            if isinstance(ts, dict):
                per[(d_, key)][ts.get("epoch_type")] += 1
                if ts.get("utc_iso"):
                    span[(d_, key)].append(ts["utc_iso"])
        if r.get("utc_iso"):
            span[(d_, "utc_iso")].append(r["utc_iso"])
    con.close()
    for k in sorted(per):
        s = span.get(k, [])
        rng = f"  [{min(s)[:19]} .. {max(s)[:19]}]" if s else "  (no decoded values)"
        bad = {e: n for e, n in per[k].items() if e in (None, "unknown", "invalid", "out_of_range")}
        print(f"  {k[0]:<14}.{k[1]:<14} {dict(per[k])}{rng}")
        if bad:
            print(f"       *** undecoded: {bad}")


# --------------------------------------------------------------------------- #
def sec5_searchable(root):
    hdr(5, "searchableStrings  (RRN extraction assumes the LAST comma token)")
    idx = {a.logical: a for a in discover(root)}
    art = idx.get("passbook.db")
    if not art:
        print("  passbook.db absent"); return
    rrn = re.compile(r"^\d{12}$")
    last_ok = mid_only = none_at_all = total = 0
    with sql.open_ro(art.abs_path) as c:
        if "UthListingEntity" not in sql.list_tables(c):
            print("  UthListingEntity absent"); return
        if "searchableStrings" not in sql.columns(c, "UthListingEntity"):
            print("  *** column 'searchableStrings' does not exist -> rrn is ALWAYS None"); return
        for (ss,) in c.execute("SELECT searchableStrings FROM UthListingEntity"):
            total += 1
            toks = [t.strip() for t in str(ss or "").split(",")]
            hits = [t for t in toks if rrn.match(t)]
            if toks and rrn.match(toks[-1]):
                last_ok += 1
            elif hits:
                mid_only += 1
            else:
                none_at_all += 1
    print(f"  rows                                  : {total:,}")
    print(f"  12-digit token IS last  (extracted)   : {last_ok:,}")
    print(f"  12-digit token present but NOT last   : {mid_only:,}   <-- RRN MISSED")
    print(f"  no 12-digit token                     : {none_at_all:,}")


# --------------------------------------------------------------------------- #
def sec6_fields(out):
    hdr(6, "FIELD COVERAGE  (parsed correctly but never shown in grid / HTML report)")
    con = sqlite3.connect(os.path.join(out, "case.db"))
    doms = [r[0] for r in con.execute("SELECT DISTINCT domain FROM records ORDER BY 1")]
    for d_ in doms:
        populated = set()
        n = 0
        for (data,) in con.execute("SELECT data FROM records WHERE domain=?", (d_,)):
            r = json.loads(data); n += 1
            for k, v in r.items():
                if k in ("provenance", "raw", "domain"):
                    continue
                if v not in (None, "", [], {}, False):
                    populated.add(k)
        shown = set(DISPLAY_COLUMNS.get(d_) or [])
        hidden = sorted(populated - shown)
        in_report = d_ in REPORT_DOMAINS
        tag = "" if in_report else "   *** DOMAIN ABSENT FROM report.html"
        print(f"\n  {d_} ({n:,} records){tag}")
        if hidden:
            print(f"    populated but NOT displayed: {', '.join(hidden)}")
        else:
            print("    all populated fields displayed")
    con.close()


# --------------------------------------------------------------------------- #
def sec7_timeline(out):
    hdr(7, "TIMELINE INTEGRITY  (stored order = report.html + CSV export order)")
    con = sqlite3.connect(os.path.join(out, "case.db"))
    ev = [json.loads(d) for (d,) in con.execute(
        "SELECT data FROM records WHERE domain='timeline' ORDER BY id")]
    doms = {r[0] for r in con.execute("SELECT DISTINCT domain FROM records")}
    con.close()
    if not ev:
        print("  no timeline events"); return
    iso = [e["utc_iso"] for e in ev]
    inversions = sum(1 for a, b in zip(iso, iso[1:]) if a > b)
    print(f"  events            : {len(ev):,}")
    print(f"  out-of-order pairs: {inversions:,}  "
          f"{'<-- NOT CHRONOLOGICAL as stored/rendered' if inversions else '(sorted)'}")
    print(f"  first 8 as stored : ")
    for e in ev[:8]:
        print(f"      {e['utc_iso'][:19]}  {e['ref_domain']}")
    rep = Counter(e["ref_domain"] for e in ev)
    print(f"  domains in timeline: {dict(rep)}")
    timestamped = {"transaction", "message", "location", "consent", "notification",
                   "search", "diagnostic", "job", "crash", "cookie", "appstate", "webcache"}
    missing = sorted((doms & timestamped) - set(rep))
    if missing:
        print(f"  *** timestamped domains NOT in the timeline: {missing}")


# --------------------------------------------------------------------------- #
def sec8_entities(out):
    hdr(8, "ENTITY RESOLUTION  (identifier appearing in >1 entity = failed merge)")
    con = sqlite3.connect(os.path.join(out, "case.db"))
    ents = [json.loads(d) for (d,) in con.execute(
        "SELECT data FROM records WHERE domain='entity' ORDER BY id")]
    con.close()
    print(f"  entities: {len(ents):,}")
    for field in ("customer_ids", "phones", "vpas", "sendbird_ids"):
        seen = Counter()
        for e in ents:
            for v in e.get(field) or []:
                seen[v] += 1
        dupes = {k: v for k, v in seen.items() if v > 1}
        if dupes:
            print(f"  *** {field}: {len(dupes)} value(s) split across entities "
                  f"(order-dependent merge): {list(dupes)[:5]}")
        else:
            print(f"  {field}: no collisions")
    unattributed = sum(1 for e in ents if e.get("txn_count", 0) == 0)
    print(f"  entities with zero attributed transactions: {unattributed}/{len(ents)}")


# --------------------------------------------------------------------------- #
def sec9_secrets(out):
    hdr(9, "SECRET HYGIENE  (token-shaped values reaching output unredacted)")
    con = sqlite3.connect(os.path.join(out, "case.db"))
    pat = re.compile(r"(eyJ[A-Za-z0-9_\-]{20,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                     r"[0-9a-f]{4}-[0-9a-f]{12}|[A-Za-z0-9+/]{60,}={0,2})")
    hits = defaultdict(list)
    for d_, data in con.execute("SELECT domain, data FROM records"):
        r = json.loads(data)
        for k, v in r.items():
            if k in ("provenance", "raw") or not isinstance(v, str):
                continue
            if pat.search(v) and not v.startswith("<redacted"):
                hits[d_].append((r.get("key") or r.get("name") or k, v[:60]))
    con.close()
    if not hits:
        print("  no token-shaped values found")
    for d_, items in hits.items():
        print(f"  {d_}: {len(items)} value(s), e.g.")
        for k, v in items[:4]:
            print(f"      {k} = {v}…")


# --------------------------------------------------------------------------- #
def main():
    if len(sys.argv) < 2:
        print(__doc__); return 2
    root = os.path.abspath(sys.argv[1])
    out = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else tempfile.mkdtemp(prefix="ptmaudit_")
    print(f"extraction : {root}\ncase out   : {out}")

    sec0_wal(root)

    c = Case(root, out, case_id="AUDIT", examiner="audit", evidence_number="AUDIT")
    c.ingest()
    results = c.parse_all()
    c.carve(); c.correlate(); c.build_timeline()
    c.close()

    sec1_parse_errors(out, results)
    sec2_tables(root, out)
    sec3_enums(root, out)
    sec4_timestamps(out)
    sec5_searchable(root)
    sec6_fields(out)
    sec7_timeline(out)
    sec8_entities(out)
    sec9_secrets(out)

    print(f"\n{H}\nDONE. case dir: {out}\n{H}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
