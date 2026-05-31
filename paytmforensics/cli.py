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
    args = ap.parse_args(argv)

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

    if args.report:
        from .report import html as htmlrep
        rp = __import__("os").path.join(args.out, "report.html")
        digest = htmlrep.generate(args.out, rp, fmt="html")
        print(f"[+] HTML report: {rp}  (sha256 {digest[:16]}…)")
    print(f"\n[+] Case written to {args.out} (case.db, manifest.json, audit.log, case_meta.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
