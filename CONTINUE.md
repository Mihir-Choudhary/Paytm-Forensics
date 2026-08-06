# CONTINUE.md — resume pointer

**Last updated: 2026-07-30 (session 1)**

Read [`STATE.md`](STATE.md) first, then [`docs/AUDIT_FINDINGS.md`](docs/AUDIT_FINDINGS.md).

---

## Next concrete action

**All 48 findings are FIXED and verified** on branch `fix/audit-findings` (session 6).
Nothing is merged. The next action is review:

1. `git diff main..fix/audit-findings` — 20 production files changed, 2 new parser modules,
   1 new regression-test file.
2. Decide the two residual warnings:
   - **PDF export** needs WeasyPrint or QtWebEngine; neither installed. Install one, or
     accept HTML-and-print.
   - **Grid virtualisation** — `RecordTableModel` holds all rows in memory. Fine at 7.9k
     (0.46s open, 0.16s filter); NFR-2's "100k+ rows" is still unproven.
3. **The F-01 fix is an evidentiary policy decision.** Databases with a populated `-wal`
   are now read from a verified scratch copy (`mode=ro`, no `immutable`) so uncheckpointed
   rows are visible and WAL-deleted rows are correctly absent. The source is only read and
   is re-hashed before/after; `test_F01_source_is_byte_identical_after_a_wal_run` proves it
   is unchanged. Confirm this satisfies your EV-1 reading before merging.
4. Merge, or cherry-pick by root cause — the commits are grouped that way.

### To re-run the audit at any time

```bash
python3 tools/audit_extraction.py /path/to/net.one97.paytm /path/to/case_out
```

That is the whole first step — it runs the full pipeline and prints all 10
reconciliation sections. Then work the sections in this order:

1. **§1 parse errors** — if any parser raised, nothing downstream is trustworthy. Fix
   understanding of that first.
2. **§0 WAL exposure** — quantifies F-01 on the real data. If it prints
   `*** ROWS HIDDEN ***`, that number is the headline of the whole report: it is how many
   real records the tool silently omits.
3. **§3 enum coverage** — any unmapped `statusKey` means the money totals in the report
   are wrong (O-1). Highest-value correctness check after F-01.
4. **§2 table reconciliation** — the "100% of plaintext artifacts parsed" claim (PRD G1).
5. **§5 searchableStrings** — RRN miss rate (O-2).
6. **§4, §6, §7, §8, §9** — timestamps, field coverage, timeline, entities, secrets.

Then generate the real report and inspect the display surface:

```bash
python3 -m paytmforensics.cli --extraction /path/to/net.one97.paytm \
    --out /path/to/case_out --case-id AUDIT --examiner audit --verify --report
```

Real-extraction path used in session 2:
`<EXTRACTION_PATH>

## If given a prebuilt `case.db` rather than an extraction

Sections 0, 2, 3 and 5 need the *source* databases and will not run. Sections 1, 4, 6, 7,
8, 9 work from `case.db` alone — call them directly, or point the harness at the case dir
and skip source-dependent sections.

---

## Carry-over context

- **Do not "fix" F-01 by removing `immutable=1`** — that is what keeps SQLite off the
  evidence and would break EV-1. The copy-db+wal-and-parse-the-copy trade-off is the
  project owner's decision. See F-01's "Recommended fix" section.
- **No production code has been changed.** This is a verification engagement. Fixes are
  deferred until the findings are reviewed. Ask before editing `paytmforensics/`.
- **Environment:** `PySide6.QtWidgets` is **not** installed here, so 3 GUI tests fail for
  environmental reasons and the Qt grids cannot be rendered. Verify "display" through
  `report.html` plus `DISPLAY_COLUMNS`. Baseline: 48 passed / 99 skipped / 3 failed.
- **99 skipped tests** all say `reason="no extraction"`. Once the real extraction exists,
  re-run with `PAYTM_EXTRACTION=/path/to/net.one97.paytm pytest -q` — that alone will
  exercise a large body of assertions that have never run on this machine. Those tests
  are themselves under audit (see F-02); treat failures as data, not verdicts.
- Some real-data tests additionally need `tests/_truth.json` (gitignored PII values,
  schema in `tests/_truth_example.json`); without it those specific assertions skip.
- **Harness lives at `tools/audit_extraction.py`** (in-repo, path-relative, smoke-tested
  against `tests/synthetic.py`). The one-off proof scripts from session 1 are listed in
  `STATE.md` and were session-scoped scratch — they are reproducible from the journal's
  hypothesis/action notes if needed again.
