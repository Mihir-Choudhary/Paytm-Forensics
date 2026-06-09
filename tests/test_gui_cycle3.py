"""Regression tests for the cycle-3 UX batch (2026-06-10). Synthetic data only.

The right-click "Filter: column = value" menu drives FilterSpec.field_equals —
these tests pin down its matching semantics (string-coerced equality, missing
keys excluded, AND across multiple fields and other filter axes).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from paytmforensics.gui.filters import FilterSpec, apply_filter


RECS = [
    {"status_label": "success", "amount": 76.0, "direction": "debit"},
    {"status_label": "failure", "amount": 76.0, "direction": "debit"},
    {"status_label": "success", "amount": 120.0, "direction": "credit"},
    {"amount": 10.0},                                # no status_label at all
]


def test_field_equals_matches_string_value():
    out = apply_filter(RECS, FilterSpec(field_equals={"status_label": "success"}))
    assert len(out) == 2
    assert all(r["status_label"] == "success" for r in out)


def test_field_equals_numeric_value_string_coerced():
    # right-click on an amount cell passes the raw float
    out = apply_filter(RECS, FilterSpec(field_equals={"amount": 76.0}))
    assert len(out) == 2
    # and the equivalent string also matches (str-coerced equality)
    assert len(apply_filter(RECS, FilterSpec(field_equals={"amount": "76.0"}))) == 2


def test_field_equals_missing_key_excludes_record():
    out = apply_filter(RECS, FilterSpec(field_equals={"status_label": "success"}))
    assert not any("status_label" not in r for r in out)


def test_field_equals_ands_with_other_axes():
    spec = FilterSpec(direction="debit",
                      field_equals={"status_label": "success"})
    out = apply_filter(RECS, spec)
    assert len(out) == 1 and out[0]["amount"] == 76.0

    spec = FilterSpec(field_equals={"status_label": "success", "direction": "credit"})
    out = apply_filter(RECS, spec)
    assert len(out) == 1 and out[0]["amount"] == 120.0


if __name__ == "__main__":
    import sys, traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    sys.exit(1 if failed else 0)
