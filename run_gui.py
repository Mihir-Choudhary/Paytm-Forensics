"""GUI entry point.

  python run_gui.py <case_dir>                 open an existing parsed case
  python run_gui.py --extraction <dir> --out <case_dir>   parse then open

Packaged as the PyInstaller entry (see paytmforensics.spec).
"""
import argparse
import os
import sys


def main():
    ap = argparse.ArgumentParser(description="PaytmForensics GUI")
    ap.add_argument("case_dir", nargs="?", help="existing case directory to open")
    ap.add_argument("--extraction", help="extracted net.one97.paytm folder (parse first)")
    ap.add_argument("--out", help="case output dir when using --extraction")
    ap.add_argument("--case-id", default="UNSET")
    ap.add_argument("--examiner", default="UNSET")
    ap.add_argument("--evidence", default="UNSET")
    args = ap.parse_args()

    if args.extraction:
        if not args.out:
            ap.error("--out is required with --extraction")
        from paytmforensics.core.case import Case
        c = Case(args.extraction, args.out, case_id=args.case_id,
                 examiner=args.examiner, evidence_number=args.evidence)
        c.ingest(); c.parse_all(); c.carve(); c.correlate(); c.build_timeline(); c.close()
        case_dir = args.out
    elif args.case_dir:
        case_dir = args.case_dir
    else:
        ap.error("provide a case_dir, or --extraction with --out")

    from paytmforensics.gui.app import launch
    sys.exit(launch(case_dir))


if __name__ == "__main__":
    main()
