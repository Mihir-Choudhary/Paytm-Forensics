#!/usr/bin/env python3
"""Pre-push gate: refuse to publish real case identifiers.

Exits non-zero if any tracked or about-to-be-tracked file contains a value from the
`tools/pii_patterns.local` list (gitignored). Written after a near-miss: a `&&` chain
pushed before the operator read the scan output, so the check must EXIT NON-ZERO rather
than merely print.

Usage:  python3 tools/check_no_pii.py && git push origin main
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATTERNS_FILE = os.path.join(HERE, "pii_patterns.local")

#: shape-based patterns that are always suspicious in a public repo
GENERIC = {
    "10-digit Indian mobile": r"(?<![0-9a-fA-F])[6-9]\d{9}(?![0-9a-fA-F])",
    "12-digit RRN-shaped":    r"(?<![0-9a-fA-F])\d{12}(?![0-9a-fA-F])",
    "19-digit id":            r"(?<![0-9a-fA-F])\d{19}(?![0-9a-fA-F])",
}
#: Files that legitimately carry SYNTHETIC fixtures matching those shapes (test data,
#: the demo generator). Being listed here waives only the shape-based rules — the literal
#: real values in pii_patterns.local are ALWAYS checked, in every file, so an allowlist
#: entry can never hide an actual leak.
SHAPE_EXEMPT = {
    "tests/synthetic.py", "tests/test_m3_carving.py", "tests/test_audit_regressions.py",
    "tests/_truth_example.json", "tools/check_no_pii.py", "tools/audit_features.py",
    "tests/test_rigorous.py", "tests/test_m10_extras.py", "tests/test_m4_gui.py",
    "docs/AUDIT_FINDINGS.md", "tools/make_demo_case.py",
    "paytmforensics/resources/bankappmanager_defaults.json",
}


def tracked():
    out = set()
    for args in (["git", "ls-files"], ["git", "ls-files", "-o", "--exclude-standard"]):
        out |= set(subprocess.run(args, capture_output=True, text=True).stdout.split())
    return sorted(f for f in out if os.path.isfile(f))


def main() -> int:
    pats = dict(GENERIC)
    if os.path.exists(PATTERNS_FILE):
        for i, line in enumerate(open(PATTERNS_FILE, encoding="utf-8"), 1):
            line = line.strip()
            if line and not line.startswith("#"):
                pats[f"local:{line[:24]}"] = re.escape(line)
    else:
        print(f"note: {os.path.basename(PATTERNS_FILE)} absent — shape checks only.\n"
              f"      Add one line per real value (name, phone, id) to check literals.\n")

    bad = []
    for f in tracked():
        try:
            txt = open(f, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for name, p in pats.items():
            # literal real values are checked everywhere; shape rules can be waived
            if name in GENERIC and f in SHAPE_EXEMPT:
                continue
            m = re.search(p, txt)
            if m:
                bad.append((f, name, m.group(0)[:24]))
    for f, name, v in bad:
        print(f"  BLOCKED  {f}: {name} -> {v!r}")
    if bad:
        print(f"\n{len(bad)} potential real identifier(s) found. NOT safe to publish.")
        return 1
    print("No real identifiers found in tracked files. Safe to publish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
