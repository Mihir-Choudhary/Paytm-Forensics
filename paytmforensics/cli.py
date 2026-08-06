"""Headless CLI runner (also used for validation/regression).

Usage:
    python -m paytmforensics.cli --extraction <folder> --out <case_dir> \
        [--case-id C123] [--examiner "A. Smith"] [--evidence E1] [--verify]
"""
from __future__ import annotations

import argparse
import json
import sys

from .core.case import Case


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="paytmforensics",
                                 description="Offline forensic parser for the Paytm Android app")
    ap.add_argument("--extraction", required=True, help="path to extracted net.one97.paytm folder")
    ap.add_argument("--out", required=True, help="output case directory")
    ap.add_argument("--case-id", default="UNSET")
    ap.add_argument("--examiner", default="UNSET")
    ap.add_argument("--evidence", default="UNSET")
    ap.add_argument("--notes", default="")
    ap.add_argument("--verify", action="store_true", help="re-verify hashes after parsing")
    ap.add_argument("--report", action="store_true", help="also write an HTML report")
    ap.add_argument("--geo", action="store_true",
                    help="also write locations.kml and locations.geojson")
    ap.add_argument("--redact-report", action="store_true",
                    help="mask PII / bank identifiers in the report (display only; "
                         "case.db keeps the full evidence)")
    ap.add_argument("--verify-audit", action="store_true",
                   help="verify the audit-log hash chain of an existing case and exit")
    args = ap.parse_args(argv)

    if args.verify_audit:
        from .core.audit import AuditLog
        rep = AuditLog.verify(__import__("os").path.join(args.out, "audit.log"))
        print(json.dumps(rep, indent=2))
        return 0 if rep["ok"] else 2

    if not __import__("os").path.isdir(args.extraction):
        ap.error(f"--extraction is not a directory: {args.extraction}")

    case = Case(args.extraction, args.out, case_id=args.case_id,
                examiner=args.examiner, evidence_number=args.evidence, notes=args.notes)
    print(f"[*] Ingesting (hashing) {args.extraction} ...")
    manifest = case.ingest()
    print(f"    {manifest['file_count']} files hashed -> manifest.json")

    print("[*] Parsing artifacts ...")
    results = case.parse_all()
    for name, n in sorted(results.items()):
        flag = "ERROR" if n < 0 else f"{n} records"
        print(f"    - {name:30} {flag}")

    if case.wal["databases"]:
        print(f"[!] WAL EXPOSURE: {len(case.wal['databases'])} database(s) have a populated "
              f"-wal.")
        print(f"    net {case.wal['net_hidden_rows']} row(s) exist only in a write-ahead log "
              f"(invisible to an immutable-only read);")
        print(f"    net {case.wal['net_deleted_rows']} row(s) would be OVER-reported as live "
              f"by an immutable-only read.")
        print(f"    (net per-table differences, not a row-level diff)")
        print(f"    This run read those databases from a verified copy with the WAL applied. "
              f"See wal_report.json.")

    print("[*] Carving deleted/residual records (free + slack space) ...")
    carved = case.carve()
    print(f"    {carved} carved records recovered")

    print("[*] Correlating entities ...")
    ents = case.correlate()
    print(f"    {ents} unified entities")

    print("[*] Building master timeline ...")
    tl = case.build_timeline()
    print(f"    {tl} timeline events")

    if args.verify:
        print("[*] Verifying source integrity (read-only check) ...")
        rep = case.verify()
        print(f"    integrity ok = {rep['ok']}  changed={len(rep['changed'])} "
              f"missing={len(rep['missing'])} added={len(rep['added'])}")

    print("\n[=] Summary by domain:")
    print(json.dumps(case.summary(), indent=2))
    case.close()

    if args.geo:
        from .report import geo as _geo
        import os as _os
        for fmt in ("kml", "geojson"):
            gp = _os.path.join(args.out, f"locations.{fmt}")
            n = _geo.export(_os.path.join(args.out, "case.db"), gp, fmt,
                            mask_sensitive=args.redact_report)
            print(f"[+] {fmt.upper()}: {gp}  ({n} fixes"
                  + (", coordinates blurred)" if args.redact_report else ")"))

    if args.report:
        from .report import html as htmlrep
        rp = __import__("os").path.join(args.out, "report.html")
        digest = htmlrep.generate(args.out, rp, fmt="html",
                                  mask_sensitive=args.redact_report)
        print(f"[+] HTML report: {rp}  (sha256 {digest[:16]}…)")
    print(f"\n[+] Case written to {args.out} (case.db, manifest.json, audit.log, case_meta.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
