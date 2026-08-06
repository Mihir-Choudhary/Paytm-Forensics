# STATE.md — PaytmForensics verification engagement

**Master journal. Single source of truth. Read this first when resuming.**
Resume pointer: [`CONTINUE.md`](CONTINUE.md) · Findings register: [`docs/AUDIT_FINDINGS.md`](docs/AUDIT_FINDINGS.md)

---

## Engagement

**Goal (user request, 2026-07-30):** read and fully understand the PaytmForensics
codebase; then, when the user supplies a real Paytm extraction / DB, compare the tool's
output against it and verify that *all data is displayed, values are correct, and
everything is parsed properly*.

**Why this framing matters:** the tool's own README already says it is WIP and
"not independently validated". So the deliverable is **not** "make it pass its own
tests" — its tests are the thing under audit too. The deliverable is an
evidence-backed statement of what the tool gets right, what it silently drops, and
what it claims in documentation but does not do.

**Verification stance adopted:** never accept a passing test as proof. For each claim,
find an *independent* ground truth (raw SQL against the source, hand-built fixtures,
generated report output) and compare. Several of this repo's completeness tests compare
the parser against a helper that shares the parser's own blind spot — see F-02.

---

## Current status (2026-07-30, after session 2)

**Headline (revised after session 5):** the tool **opens 53% of the files** in the
evidence; of the SQLite rows it does reach it misses a further 306 to the WAL (F-01),
including four domains at 0%; and it **hashes 100%** of the extraction into a manifest
certifying the integrity of data the case never examined. PRD goal G1 ("parse 100% of the
plaintext artifacts") is not met.

F-01 is live on the real extraction and dominates the *row-level* losses.
Four domains parse at **0%** (location history, consents, search history, bank config)
because their table *schema* lives in the WAL. The passbook ledger is missing its **most
recent 14 transactions** — all activity after <date>, against real activity to
<date> — which is **30.8% of settled value (₹<amount> of ₹<amount>)**.
Separately, 74 rows the app had *deleted* are reported as `origin=live`.

## Phase table

| Phase | State |
|---|---|
| Codebase read (all ~8.5k lines, 20 parsers + carver + correlate + report + GUI) | **done** |
| Static findings register | **done** — 15 issues, see `docs/AUDIT_FINDINGS.md` |
| Empirical confirmation without real data | **done** — 6 issues proven by execution |
| Reconciliation harness built + smoke-tested | **done** — `audit_extraction.py` (see below) |
| Run against the real extraction | **done (session 2)** — 4 new findings, F-01 confirmed live |
| Per-feature / per-filter rigorous audit | **done (session 3)** — 311 checks, 13 new findings |
| GUI visual + interaction audit | **done (session 4)** — 88 checks + 28 screenshots inspected by eye, 10 new findings |
| Artifact coverage audit (file level) | **done (session 5)** — 47% of files read by nothing; 6 new findings |
| **All 48 findings FIXED and re-verified** | **done (session 6)** — branch `fix/audit-findings`; every harness green |
| Hide-sensitive-data toggle + re-verification | **done (session 7a)** — 6 defects found while building it |
| Hardening pass (never-audited paths) | **done (session 7b)** — F-49..F-52; suite 182 passed |

**Session 6 fixed all 48 findings** on branch `fix/audit-findings`, by root cause, with
26 new regression tests written to fail against the pre-fix code. Sessions 1–5 were
verification only; the audit register below is preserved as the record of what was found.

---

## Architecture as actually implemented

Pipeline (identical headless and GUI — both are shells over `core/case.py:Case`):

```
ingest()        integrity.build_manifest  -> sha256+md5 of every file -> manifest.json
parse_all()     artifact.discover -> parsers.REGISTRY (each parser yields Records)
carve()         carving/sqlite_carver over UNALLOCATED regions only, deduped vs live
correlate()     correlate/entities  -> merges Person records into `entity` domain
build_timeline() correlate/timeline -> merges timestamped records into `timeline` domain
verify()        re-hash, assert byte-identical
```

Storage: one generic table `case.db:records(id, domain, origin, source_file,
source_table, data JSON)`. Every read path (GUI grids, HTML report, exports) goes
through `gui/datasource.py:DataSource`, so **`DISPLAY_COLUMNS` in that file is the
single chokepoint that decides what a human ever sees** — this is the root of the
field-coverage findings (F-06).

Read-only mechanism: `ingest/sqlite_ro.py:open_ro` uses
`file:<path>?mode=ro&immutable=1`. **This is the source of the most serious finding
(F-01)** — `immutable=1` is what makes SQLite ignore the `-wal`.

---

## Findings summary

Full detail, evidence and repro in [`docs/AUDIT_FINDINGS.md`](docs/AUDIT_FINDINGS.md).

| ID | Severity | Status | One line |
|---|---|---|---|
| F-01 | **critical** | CONFIRMED by execution | `immutable=1` makes the parser silently ignore uncheckpointed `-wal` rows |
| F-02 | **critical** | CONFIRMED by reading | completeness tests can't catch F-01 — both sides use `open_ro` |
| F-03 | high | CONFIRMED by execution | "master chronological timeline" is not chronological in report.html / CSV |
| F-04 | high | CONFIRMED by execution | timeline omits `job` events though FR-14 requires them |
| F-05 | high | CONFIRMED by execution | entity merge is order-dependent; same input → 1 or 2 entities |
| F-06 | high | CONFIRMED by execution | fields parsed correctly never reach the report (`error_message`, whole `person` domain) |
| F-07 | high | CONFIRMED by execution | METHODOLOGY §2 claims report surfaces rowid/offset/confidence/hash — it does not |
| F-08 | medium | CONFIRMED by execution | date-range filter silently returns 0 rows on `cookie` / `crash` grids |
| F-09 | medium | CONFIRMED by reading | README says `message` = non-payment messages; parser emits all, so payments appear in 2 domains |
| F-10 | medium | CONFIRMED by reading | carve dedup ignores message-domain RRNs → live RRNs can be labelled `carved` |
| F-11 | medium | CONFIRMED by reading | HTML report renders *every* row by default (CLI passes no `table_limit`) |
| F-12 | low | CONFIRMED by reading | `search_state.py` computes a friendly query label then discards it |
| F-13 | low | CONFIRMED by reading | stale comment in `transactions.py` contradicts the code directly above it |
| F-14 | low | CONFIRMED by reading | `identity.py` `account_age_text` is never assigned (always None for subject) |
| F-15 | low | CONFIRMED by reading | `global_search` and `FilterSpec.matches` search different field sets; docstring claims they match |
| F-16 | **high** | CONFIRMED on real data | rows the app deleted (per WAL) are reported as `origin=live, confidence=1.0` — 71 in `pai_signal` alone |
| F-17 | medium | CONFIRMED on real data | `txnCategory=2` labelled "cashback" but app's own `txnTag` says "Money Received" for 3 of 4 rows |
| F-18 | medium | CONFIRMED on real data | 7 `COMPLETED` UPI requests (₹<amount>, all with RRNs) get `direction=None, settled=False` — counted in neither total |
| F-19 | **high** | CONFIRMED on real data | 211 diagnostic GPS fixes appear in no table and nowhere in `report.html`; Location section reads "(1)" |
| F-20 | **high** | CONFIRMED by execution | `date_to` compares strings → every date range silently loses its final day |
| F-21 | **high** | CONFIRMED by execution | unpadded date (`2026-5-1`) lexically excludes almost everything, silently |
| F-22 | medium | CONFIRMED by execution | amount/direction filters silently empty grids whose domain lacks those fields |
| F-23 | low | CONFIRMED by execution | `field_equals` matches an absent field against the string `"None"` |
| F-24 | medium | CONFIRMED by reading | FR-G3 "save filter presets per case" not implemented at all |
| F-25 | medium | CONFIRMED by execution | CLI accepts a nonexistent extraction path, exits 0 with an empty case |
| F-26 | medium | CONFIRMED by execution | passbook-only counterparties get no entity (FR-13); ₹<amount> missing from counterparty totals here |
| F-27 | medium | CONFIRMED by execution | no audit-log verifier shipped (EV-5); chain itself is sound |
| F-28 | medium | CONFIRMED by execution | 8 notifications timestamped in the future (`expiry` used as event time) |
| F-29 | low | CONFIRMED by execution | non-numeric amount input silently discarded — filter looks applied but isn't |
| F-30 | low | CONFIRMED by execution | export target goes stale after visiting Dashboard → exports the previous grid |
| F-31 | low | CONFIRMED by execution | CSV timestamp is a JSON blob in one cell — not sortable in a spreadsheet |
| F-32 | medium | CONFIRMED by execution | `TBL_CHANNELS`(41) / `TBL_MESSAGE_HISTORY`(11) never parsed; 30 conversations invisible (FR-4) |
| F-33 | **high** | CONFIRMED visual+data | same merchant split across entities by name/case (`FoodCo`/`Foodco`); 4 parties → 8 entities |
| F-34 | **high** | CONFIRMED visual+data | "212 GPS fixes" are 4 distinct coordinates; KML/GeoJSON still draw a `movement_path` |
| F-35 | **high** | CONFIRMED visual+data | chat bubble headed "Paid" with body "Sent you ₹X" — 52/84 messages imply the wrong direction of funds |
| F-36 | medium | CONFIRMED visual+data | Timeline grid: `ref_domain` gets 996px while `utc_iso`/`summary` truncate to 100px |
| F-37 | medium | CONFIRMED by execution | no grid is sortable — `setSortingEnabled` never called, no `sort()` override |
| F-38 | medium | CONFIRMED computed | WCAG AA contrast failures both themes: dim text 3.48/3.08:1, white-on-accent 3.75:1 (selected row + primary buttons) |
| F-39 | low | CONFIRMED visual | map caption claims online interactive tiles while the offline fallback is showing |
| F-40 | low | CONFIRMED by execution | nav group headers hardcoded `Qt.gray`, ignore the palette |
| F-41 | low | CONFIRMED visual | domains parsed to zero are hidden from nav/dashboard — "none found" vs "not attempted" indistinguishable |
| F-42 | low | CONFIRMED visual | Export menu bar floats above the sidebar as an orphaned strip |
| F-43 | **high** | CONFIRMED by execution | **327 of 698 files (46.8%) read by nothing**; 6 PRD in-scope groups at 0% |
| F-44 | **high** | CONFIRMED by execution | no parser at all for FR-3 contacts db, FR-9 reminders, FR-8 in_app_notification_model, RealtimeSmsUploadDb, pai_signal |
| F-45 | medium | CONFIRMED by execution | FR-12 catalogues 2 of 21 encrypted `shared_jsons`; 19 invisible |
| F-46 | medium | CONFIRMED by execution | `shared_prefs` is a 7-name allowlist against 38 files; 31 unread incl. Sendbird session key + FB access token |
| F-47 | medium | CONFIRMED by execution | carver targets 4 of 20 DBs and **never opens a `-wal`** — likely why carving recovered 0 |
| F-48 | low | CONFIRMED by execution | `Account` model defined but never emitted by any parser (FR-1 linked bank account) |

**Closed by session 2 (no defect found):** `statusKey` is fully mapped, so money totals
are safe from the unmapped-success-code risk; RRN extraction is 44/44; chat direction logic
fires correctly; entity resolution held with zero identifier collisions; the subject is
correctly identified; timestamps decode cleanly. See the "Verified CORRECT" section of the
findings register — the audit is not uniformly negative.

**Still latent (did not bite on this input, keep open):** F-05 order-dependence, F-10 carve
dedup (moot — carving recovered 0), the mobile-not-a-join-key gap.

---

## Work log

### Session 1 — 2026-07-30

**Done:** full codebase read; docs (README / METHODOLOGY / PRD / IMPLEMENTATION_PLAN)
read and cross-checked against implementation; test suite run; 15 findings registered;
6 confirmed by execution; reconciliation harness written and smoke-tested.

**Environment note:** `PySide6` is installed but **`PySide6.QtWidgets` is not**, so
3 GUI tests fail for environmental reasons, not defects
(`test_m6_packaging.py::test_entrypoints_import`,
`test_m8_mapchat.py` ×2). Baseline on this machine: **48 passed, 99 skipped, 3 failed**.
The 99 skips are all `reason="no extraction"` — i.e. *the entire real-data half of the
suite has never run here*. Consequence: "display" verification was done via
`report.html` (generatable headlessly) plus reading `DISPLAY_COLUMNS` / `models.py`;
the Qt grids themselves were **not** rendered.

**Hypothesis → action → signal → conclusion** for the confirmed findings:

1. **H:** PRD FR-7 wants the WorkManager WAL parsed, but `immutable=1` may make SQLite
   ignore `-wal` entirely.
   **A:** built a WAL-mode DB, checkpointed 1 row, wrote 5 more that stayed in the
   `-wal`, copied db+wal+shm as a forensic extraction would, read it both ways
   (`scratchpad/wal_check2.py`), then ran the *full pipeline* over it
   (`scratchpad/wal_pipeline.py`).
   **S:** `open_ro` → 1 row. `mode=ro` without `immutable` → 6 rows. Full pipeline
   reported `transactions.passbook: 1`, total amount **1/511th of the true settled total** (a
   purpose-built fixture, not case data).
   No error, no warning; the `-wal` *is* discovered and hashed (kind `other`) but never read.
   **Ruled in:** F-01 is real and is a silent-data-loss bug, not a theoretical one.
   **Ruled out:** dropping `immutable=1` as the fix — it is what keeps SQLite off the
   source. Correct fix is parse-a-copy (already sanctioned by the PRD's own EV-1
   wording, "operate on a working copy when any tooling could write"). That trade-off
   is the user's call, so it is recorded, not applied.

2. **H:** the completeness tests would have caught F-01.
   **A:** read `tests/test_rigorous.py:_raw_count` and `tests/synthetic.py:_db`.
   **S:** `_raw_count` reads through the same `sql.open_ro`, so expected and actual
   share the blind spot; and `synthetic.py` never sets `journal_mode=WAL`, so no
   fixture can exercise it. **Ruled in:** F-02 — a gap in the *verification method*.

3. **H:** the timeline is sorted somewhere before it reaches output.
   **A:** ran the pipeline on `tests/synthetic.py` and printed `timeline` in stored
   order, then generated `report.html` and scraped the rendered rows
   (`scratchpad/synth_run.py`, `scratchpad/report_check.py`).
   **S:** stored/rendered order is Aug 15 → Aug 16 → **Aug 15** → Aug 16 → 08:31:56 →
   **08:31:53**: grouped by domain (the `builders` dict iteration order), then rowid.
   **Ruled in:** F-03. Sorting exists only in `gui/dashboard.py` and
   `gui/timelineview.py`, i.e. only on screen — the court-facing artefacts are unsorted.
   Also observed 0 `job` events → F-04.

4. **H:** `entities._merge_people` implements the union-find its comment claims.
   **A:** hand-built 3 person records forming a transitive chain A—phone—B—vpa—C and
   ran `entities.build` twice with different row order (`scratchpad/entity_check.py`).
   **S:** order `[A,B,C]` → **1** entity; order `[A,C,B]` → **2** entities, with
   `x@ptybl` appearing in *both*. **Ruled in:** F-05. The loop `break`s on first hit
   and never re-checks earlier entities. `test_I_no_duplicate_customer_ids_across_entities`
   only inspects `customer_ids`, so a split on phone/VPA passes unnoticed. Knock-on:
   `build()` does `by_vpa[v] = e`, so last-writer-wins and transactions get attributed
   to whichever fragment came last.

5. **H:** everything parsed reaches the report.
   **A:** synthetic passbook row carries `errorCode=1103`; checked case.db then grepped
   the generated report (`scratchpad/report_check.py`).
   **S:** case.db has `error_message = "You have entered incorrect passcode. Kindly
   retry."` — **absent from report.html**. `person` domain absent from `REPORT_DOMAINS`
   entirely. No `rowid` / `byte_offset` / per-record `confidence` / `ingest_sha256`
   anywhere in the report. **Ruled in:** F-06 and F-07.

6. **H:** date filtering works on every grid (FR-G3).
   **A:** ran `FilterSpec(date_from=…, date_to=…).matches()` against cookie/crash/txn
   records all inside the range.
   **S:** `record_utc` only inspects `utc_iso`/`timestamp`/`last_enqueue`; cookies use
   `created`, crashes use `start_time` → both return `None` → filter rejects the row.
   3 in-range records in, 1 out. **Ruled in:** F-08 — a *silent wrong-empty result*,
   the worst failure mode for an examiner.

**Paused at:** harness ready, awaiting the real extraction.
**Start next time with:** the one command in `CONTINUE.md`.

---

## Tooling built this engagement

`tools/audit_extraction.py` is **in-repo and path-relative**. The proof scripts were
session scratch (under `/tmp/claude-1000/.../scratchpad/`); each is reproducible from the
hypothesis/action notes above if it is ever needed again.

| File | Purpose |
|---|---|
| `tools/audit_extraction.py` | **the main harness (kept).** 10-section reconciliation of a real extraction vs tool output. Smoke-tested on the synthetic fixture. |
| `wal_check2.py` | minimal proof that `immutable=1` hides `-wal` rows |
| `wal_pipeline.py` | proof the loss propagates through the full pipeline into money totals |
| `entity_check.py` | proof the entity merge is order-dependent |
| `synth_run.py` | runs the pipeline on `tests/synthetic.py`, dumps timeline in stored order |
| `report_check.py` | generates report.html and checks field coverage + timeline order |

Harness sections: 0 WAL exposure · 1 swallowed parse errors · 2 per-table row
reconciliation · 3 enum coverage · 4 timestamp census · 5 `searchableStrings` RRN
assumption · 6 field coverage vs `DISPLAY_COLUMNS`/`REPORT_DOMAINS` · 7 timeline
ordering + domain coverage · 8 entity identifier collisions · 9 secret hygiene.

Section 2 deliberately excludes the derived domains (`timeline`, `entity`, `carved`)
because they re-carry the *original* record's provenance and would otherwise
double-count the source table. `passbook.db::UthInstrumentEntity` is whitelisted as a
legitimate join-only input. `chatDb.db::TBL_USERS` reporting `1/2` rows is
correct-by-design (the subject row `isMe=1` is handled by the identity parser instead).


### Session 2 — 2026-07-30 (real extraction)

**Input:** `<EXTRACTION_PATH>
— 698 files, 38 MB, **18 databases with a non-empty `-wal`**.

**Done:** ran `tools/audit_extraction.py` end-to-end; generated the real `report.html`
(2.57 MB) and inspected the display surface; added findings F-16..F-19; recorded what is
verifiably correct.

**Hypothesis → action → signal → conclusion:**

1. **H:** F-01 might be latent if the extraction is checkpointed.
   **A:** enumerated `-wal` files, then per-table counts immutable-view vs a db+wal copy.
   **S:** 18 populated WALs; four tables invisible *entirely* (schema in WAL); passbook
   44/58. Verified the counts are not a stale-`-shm` artifact by comparing db+wal+shm,
   db+wal, and db-alone — identical.
   **Ruled in:** F-01 live and dominant.
   **Found a flaw in my own harness:** §0 enumerated tables from the *immutable* view, so
   tables whose schema lives only in the WAL were never compared and the first pass
   under-reported the loss. Fixed; re-measured.

2. **H:** the WAL delta is uniform across the ledger.
   **A:** diffed `sourceTxnId` sets and computed date ranges + settled totals.
   **S:** the 14 missing rows are **all** the most recent; the tool's ledger ended ~2 months
   short of the newest real transaction; 30.8% of settled value unreported, including the
   largest single transaction in the set.
   **Ruled in:** the failure mode is "the last two months are missing", not "a few rows are
   missing" — much more likely to produce a wrong investigative conclusion.

3. **H:** all WAL deltas are losses.
   **A:** noticed *negative* deltas and checked them.
   **S:** `pai_signal` 71 rows in the main file, 0 after WAL replay — the WAL records their
   deletion. **Ruled in:** F-16, a distinct evidentiary defect (deleted → `origin=live`).

4. **H:** `TXN_CATEGORY[1]="food_and_beverages"` is over-fitted to a small sample (40/44
   rows landing on "food" looked implausible).
   **A:** cross-checked the inferred label against the app's own `txnTag` column.
   **S:** **I was wrong about category 1** — 39/39 corroborated by `txnTag='🥘 Food'`.
   But category **2** is mislabelled: `txnTag='💵 Money Received'` on 3 of 4 rows.
   **Ruled in:** F-17. **Ruled out:** my original suspicion about category 1.

5. **H:** non-SUCCESS chat statuses are all genuine failures.
   **A:** listed every non-SUCCESS payment message with its RRN/uniqueKey.
   **S:** 7 `UPI_REQUEST`/`COMPLETED` rows, all with RRN *and* uniqueKey — money moved.
   All stored `direction=None, settled=False`. **Ruled in:** F-18, ₹<amount> in neither total.

6. **H:** the Location grid reflects the location evidence in the case.
   **A:** counted lat/lon-bearing records per domain, then read the generated report's
   actual `<th>` list for Diagnostics and Location.
   **S:** 1 location record vs **211** diagnostics carrying coordinates; Diagnostics
   columns contain no lat/lon. **Ruled in:** F-19.
   **Method note:** naive `grep` for `error_message`/`latitude` in the report produced
   *false positives* (config key names; the Location table's own header). Both were
   re-verified by parsing the actual section headers — never trust a substring match on a
   2.5 MB document.

7. **H:** carving would recover the deleted rows.
   **A:** ran the carver's region walk over all four targets.
   **S:** 0 records. Free space exists (241 KB in chatDb) but nothing reconstructable;
   freelist empty on all four. **Ruled in:** the README/PRD "≈2× recovery" claim is unmet.
   **Likely root cause shared with F-01:** `SqliteCarver` reads only the main db file, so
   WAL-resident deletions are unreachable. Fixing F-01 via a db+wal copy would plausibly
   make carving productive too.

**Paused at:** findings complete and recorded; **no production code changed**.
**Start next time with:** the fix-triage question in `CONTINUE.md`.


### Session 3 — 2026-07-31 (per-feature rigorous audit)

**Scope:** the user asked for each feature and filter to be audited separately and
rigorously. Built two new harnesses beside the session-2 one; **311 checks total**.

| tool | scope | result |
|---|---|---|
| `tools/audit_features.py` | filters, pure fns, datasource, exports, integrity, CLI, GUI runtime | 213 checks — 196 pass / 7 fail / 10 warn |
| `tools/audit_parsers.py` | every parser + view, values vs independent SQL | 98 checks — 82 pass / 11 fail / 5 warn |

**Environment change:** installed `PySide6-Essentials`, so `QtWidgets` is now available and
**the GUI was driven at runtime offscreen for the first time**. QtWebEngine remains absent,
so the Leaflet map path and the Qt PDF backend are still untested; the offline map fallback
*was* tested and works. Note `shiboken6` is 6.11.1 against dist-packages PySide6 6.10.2 —
no binding problems observed, but suspect that mismatch before reporting a Qt oddity as a
product defect.

**Two defects in my own audit code, found and fixed rather than filed as product bugs:**
- asserted `timestamps.decode(1723742756.754)` → `unix_ms`; **`unix_s` is correct**, since
  `int()` truncates the float to 10 digits. My expectation was wrong.
- an unconditional note string made a PASS print "no message flagged" when 84/84 were.

**Hypothesis → action → signal → conclusion:**

1. **H:** date filtering works apart from the F-08 domain gap.
   **A:** boundary-tested `date_to`/`date_from` against a record at 12 May 07:15.
   **S:** `date_to='<date>'` → **False**. Comparison is string-vs-ISO, so the final day
   is always excluded; and `date_from='2026-5-1'` → **False** because `'0'<'5'` lexically.
   **Ruled in:** F-20, F-21. The date filter is the least trustworthy control in the GUI.

2. **H:** the F-08 pattern (filter field only some domains populate) is isolated.
   **A:** tested every filter field against a domain that lacks it.
   **S:** `amount_min=0` and `direction=` both empty the config/pref/cookie grids.
   **Ruled in:** F-22 — same wrong-empty-result family, three fields wide.

3. **H:** entity totals reconcile with transaction totals.
   **A:** summed entity `total_received`/`total_paid` against settled credits/debits.
   **S:** paid matched to the cent; received was ₹<amount> vs ₹<amount>. Traced to one
   transaction whose counterparty VPA and name exist in **no** `person` record.
   **Ruled in:** F-26 — entities are seeded only from `person`, so passbook-only
   counterparties are never entities. Small here (chat-heavy device); would dominate on a
   merchant-payment-heavy one.

4. **H:** the theme toggle is fragile (it deletes and rebuilds pages, then re-navigates).
   **A:** toggled from the transaction grid, twice consecutively, and from the chat, map and
   timeline views.
   **S:** all five **passed**. **Ruled out** as a defect — the riskiest-looking code path in
   the GUI is actually sound.

5. **H:** the audit-log hash chain might not verify on real data.
   **A:** wrote an independent verifier; ran it on the real 33-entry log; then tampered with
   one `parse` entry and re-ran.
   **S:** verifies end to end with 0 breaks; tampering produced 26 downstream mismatches.
   **Ruled out** as a defect — but **ruled in F-27**: the tool ships no verifier, so a third
   party cannot perform this check. Mechanism sound, not exposed.

6. **H:** parsers may be mis-transcribing values, not just missing rows.
   **A:** field-by-field comparison of all 44 passbook transactions against independent SQL.
   **S:** **0 mismatches** across amount, direction, status, category, VPA, RRN, tag and
   timestamp. **Ruled out** — transcription is exact. The tool's problems are completeness
   (F-01), presentation (F-06/F-19) and interpretation (F-17/F-18), *not* transcription.

7. **H:** the project's own suite would show at least some strain on real data.
   **A:** `PAYTM_EXTRACTION=<real> pytest -q` (first time the real-data half has ever run
   on this machine — 99 previously-skipped tests).
   **S:** **138 passed, 12 skipped, 0 failed.** Fully green on the same extraction where the
   tool drops 30.8% of settled transaction value, parses four domains at 0%, and reports 74
   deleted rows as live.
   **Ruled in:** F-02 in the strongest possible form. The suite validates that the code does
   what the code does — `_raw_count` shares the parser's WAL-blind reader, no fixture sets
   `journal_mode=WAL`, and no test compares a *rendered* artefact against the data it claims
   to present. Recorded as the register's capstone.

**Paused at:** three harnesses in `tools/`, all clean and re-runnable; 32 findings recorded;
**no production code changed**.
**Start next time with:** the fix-triage list in `CONTINUE.md`.


### Session 4 — 2026-07-31 (GUI visual + interaction audit)

**Why this session existed:** session 3 drove the GUI programmatically and **every GUI check
passed**. The user asked to involve the GUI again, which was the right instinct — row counts
and absent exceptions cannot see a contradiction on screen. This session **rendered 28
screenshots** (12 views × 2 themes, plus search and a 900×600 stress size) and I inspected
them by eye, then confirmed each observation numerically.

`tools/audit_gui_visual.py` — 88 checks: 75 pass / 8 fail / 2 warn / 3 info. Screenshots in
`scratchpad/real/shots/` (session scratch; re-runnable).

**Hypothesis → action → signal → conclusion:**

1. **H:** the GUI is fine, because session 3's 40-odd GUI checks all passed.
   **A:** rendered every view in both themes and looked at the pixels.
   **S:** the dashboard's "Top counterparties" panel lists **`FoodCo` (10 txns)** and
   **`Foodco` (3 txns)** as two parties; the chat list shows `Bistro`, `Bistro Limited`
   and `Bistro Media Private Limited` as three conversations.
   **Ruled in:** F-33. Then quantified: 4 real merchants across 8 entities, two split by
   letter case alone. **Lesson:** programmatic GUI checks proved liveness, not correctness.

2. **H:** "212 GPS fixes" means 212 positions.
   **A:** the offline map drew only **two dots** — so I counted distinct coordinates.
   **S:** 4 distinct, three of them within ~10 m (128 + 82 + 1 + 1 occurrences).
   **Ruled in:** F-34. The location evidence is two places; the tool labels it 212 fixes and
   exports a `movement_path` LineString. FR-5 explicitly asks for coincident-fix
   de-duplication, which is not implemented.

3. **H:** the chat bubbles read correctly.
   **A:** read the rendered thread.
   **S:** a bubble headed **"₹<amount> · Paid"** with body **"Sent you ₹<amount>"**. 52 of 84 messages
   affected; 4 headed "Paid" while their own status glyph says failed.
   **Ruled in:** F-35 — a **direction-of-funds** contradiction, the most consequential class
   of error this tool could induce. Cause: header derives from `sender_id`, body is Paytm's
   stored text written from the *recipient's* perspective.

4. **H:** table layout is fine because the main grid looked right.
   **A:** measured every column width in the timeline table.
   **S:** `ref_domain` 996px of a 1296px viewport; `utc_iso` and `summary` stuck at the
   100px default and truncated. `TimelineView` never calls `resizeColumnsToContents()`,
   unlike `_on_nav`.
   **Ruled in:** F-36 — in the master timeline the time of day is invisible while the widest
   column repeats one word 299 times.

5. **H:** grids are sortable.
   **A:** checked `isSortingEnabled()` and whether the model overrides `sort()`.
   **S:** both no. **Ruled in:** F-37. With F-03 (timeline not stored chronologically) there
   is **no way to get a chronologically sorted transaction list in the UI**.

6. **H:** the palette is accessible.
   **A:** computed WCAG 2.1 ratios for 14 fg/bg pairs per theme.
   **S:** body text 13–16:1 (excellent), but `text_dim` 3.48:1 dark / 3.08:1 light and
   white-on-accent 3.75:1 — the latter being **the selected table row** and the primary
   buttons. **Ruled in:** F-38.

7. **H:** layout breaks at small window sizes.
   **A:** rendered 900×600 in both themes.
   **S:** holds up — sidebar keeps width and scrolls, filter card reflows, splitter
   survives. **Ruled out** as a defect.

**Also ruled out this session** (checked, no defect): both themes render all 12 views with no
clipping/overlap/unstyled widgets; the main table page sizes columns correctly; the detail
panel is the best-executed part of the UI (all provenance fields + full SHA-256); tooltips
carry provenance on every cell; the timeline ribbon spans the right range with correct
per-domain colours; keyboard navigation works; 7,027-row grid opens in 0.46s and filters in
0.16s.

**Paused at:** four harnesses in `tools/`, 42 findings recorded; **no production code
changed**.


### Session 5 — 2026-07-31 (artifact coverage)

**Why:** the user said things were still being missed. They were right, and the gap was in
my own method: sessions 2–4 reconciled *SQLite tables* and *GUI surfaces*, and **never asked
which of the 698 files the tool opens at all**. Non-SQLite artifacts — the majority of the
PRD's in-scope list — had never been checked.

**Method:** `tools/audit_coverage.py` monkeypatches `builtins.open` and `sqlite3.connect`
for a full pipeline run, separating what `ingest()` hashes from what parsers actually read.

**Hypothesis → action → signal → conclusion:**

1. **H:** table-level reconciliation implies file-level coverage.
   **A:** instrumented every file open across ingest / parse / carve.
   **S:** 698 files hashed, **370 opened by a parser, 327 read by nothing (46.8%)**. Six
   PRD §5.1 groups at exactly 0%: all 28 `shared_jsons`, `files/datastore`,
   `in_app_notification_model`, `PersistedInstallation`, `AppEventsLogger`, all 11 IndexedDB
   files. **Ruled in:** F-43. **Ruled out:** my prior assumption that "0 parse errors" said
   anything about coverage — nothing crashed *because* half the files are never touched.

2. **H:** every PRD functional requirement has a parser.
   **A:** walked `parsers.REGISTRY` collecting `needs`, cross-referenced the FR list.
   **S:** FR-3 (`contacts` db), FR-9 (`discoveryDb.TBL_REMINDERS`) and FR-8
   (`in_app_notification_model`) have **no parser registered at all**. Also unparsed:
   `RealtimeSmsUploadDb`, `pai_signal` (71 rows), `AppEventsLogger`.
   **Ruled in:** F-44. Note `contacts` is a *carve* target but not a *parse* target — the
   tool carves a database it never reads. On this extraction both `contacts` and
   `discoveryDb` are also empty in the immutable view, so **F-01 was masking F-44**: two
   independent defects, either sufficient to lose the data.

3. **H:** FR-12's encrypted catalogue is complete.
   **A:** classified all 28 `shared_jsons` by content shape.
   **S:** 21 are base64 ciphertext, 0 plaintext; **only 2 are catalogued**, by hardcoded
   filename. **Ruled in:** F-45 — the Encrypted Artifacts section shows 5 when the true
   count is 24.

4. **H:** carving returned 0 because the databases are clean (session 2's conclusion).
   **A:** read `CARVE_TARGETS` and `SqliteCarver.__init__`.
   **S:** only **4 of 20** databases are carved, and **no `-wal` is ever opened** — despite
   18 populated WALs, which are rings of old page images and the richest source of recent
   deletions. **Ruled in:** F-47, and it **revises session 2's explanation**: the zero is at
   least partly a scope limit, not purely a source-state limit. METHODOLOGY §8 discloses the
   latter and not the former.

5. **H:** the model layer is fully wired.
   **A:** compared every `Record` subclass's `domain` default against emitted domains.
   **S:** `Account` is never constructed anywhere (`grep "Account("` → nothing).
   **Ruled in:** F-48. **Ruled out** for `Consent`/`SearchQuery`: parsers exist; their zero
   counts are F-01, not dead code. My first version of this check instantiated each
   dataclass, raised on the required `provenance` arg, and silently reported *nothing* —
   fixed to read the field default instead.

**Paused at:** five harnesses in `tools/`, 48 findings; **no production code changed**.


### Session 6 — 2026-07-31 (fix all findings)

Branch `fix/audit-findings`, off a commit that captured the audit-only state so the
before/after is reviewable. Fixed **by root cause**: F-01+F-16+F-47 share "nothing opens a
-wal"; F-05+F-26+F-33 share "entity resolution needs one normalise-then-union pass";
F-08+F-15+F-20..F-24+F-29 are all `FilterSpec`.

**Verification (real extraction):** parsers 101/101 · features 227/228 · visual 90/94 ·
coverage all 12 PRD groups at 100% · project suite **164 passed, 0 failed** · 26 new
regression tests. Two residual warnings are environmental (no PDF backend) and
architectural (grid not virtualised) — both stated, neither hidden.

**Evidence recovered:** transactions 86→100, the ledger now runs to the extraction date
(previously it stopped ~2 months short), settled paid value +78%, consents 0→4,
searches 0→1, locations 1→21,
encrypted artifacts 5→30, prefs 129→229, timeline out-of-order pairs 48→**0**, plus new
`account` and `channel` domains.

**Five defects in my own fix work**, each caught by a harness or an existing test:
1. a **modal** QMessageBox in the new filter warning hung every headless run;
2. channel records inflated the Chats count 84→125 before getting their own domain;
3. reading Data*.xml headers leaked the key name `datak` — caught by the project's own
   `test_J_no_plaintext_secret_in_encrypted_records`, now records shape only;
4. the new `account` domain was unreachable from the sidebar;
5. **the coverage harness inflated coverage to 90%** because the carver's magic-byte sniff
   opens every file. Corrected to count parser reads only — the honest figure is 27.3%
   unread (UI animations, sounds, LevelDB/cache bookkeeping), not 9.7%.

**Lesson carried forward:** after a fix, a harness written against the *old* behaviour will
happily report PASS for the wrong reason. Every check that touched changed code was
re-read and re-pointed at the outcome rather than the old implementation detail.

**Paused at:** all findings fixed and verified; branch not merged.
**Start next time with:** review the diff on `fix/audit-findings`, decide on the two
residual warnings (PDF backend, grid virtualisation), then merge.


### Session 7 — 2026-07-31 (masking feature + hardening)

**Built:** a Hide-sensitive-data toggle (`core/privacy.py` + topbar button), OFF by default,
masking PII / bank identifiers / coordinates across every grid, dashboard, chat, map,
timeline, detail panel, global search, exports and (via `--redact-report`) the HTML report.
Display layer only — `case.db` always keeps the full evidence, and filters/search still
match the real values.

**Re-verified correlation on real data:** 58/58 passbook→instrument joins with IFSC
resolution, 0 dedup leaks, 84/84 counterparties resolved via TBL_USERS × cross-ref,
100/100 transactions and 84/84 messages attributed, 30 message-less channels surfaced,
timeline across 10 domains all carrying provenance hashes.

**Ten defects found and fixed while doing this** — the ones worth remembering:
1. masking latitude to a *string* would have crashed the map (it does arithmetic on lat/lon);
2. `pincode` was masked as a coordinate → `500081.0`;
3. the IFSC pattern never matched after `_` (`\b` vs word chars), and the PSP handle / bank
   code survived — both name the bank;
4. `raw` masking handled only top-level strings, leaking a customer id nested in
   `raw['cart']` (F-52);
5. the redacted **report** still leaked a name from a pref value containing
   `"displayName":"…"` — free-text masking is pattern-based and names have no shape, so a
   name-assignment pattern plus literal replacement of case-harvested names was needed;
6. **the geo export bypassed masking entirely** (F-49) — it reads case.db directly;
7. the GUI crashed with a raw traceback on a wrong folder (F-50);
8. the PyInstaller spec collected only the parsers, not the lazily-imported carver /
   correlate / report layers (F-51);
9. Qt's implicit initial sort made the timeline newest-first while the report was
   oldest-first;
10. **and a process failure of mine: an `&&` chain pushed to the public repo before I read
    the PII scan output, publishing two real identifiers.** The scan printed a warning and
    exited 0. `tools/check_no_pii.py` now EXITS NON-ZERO so it can gate a push, combines
    shape rules (with hex boundaries, so MongoDB ObjectIds are not read as phone numbers)
    with a gitignored `tools/pii_patterns.local`, and is proven to block a planted value.
    **The identifiers remain in commit `e194edb` in public history** — removing them needs
    a force-push, which is the owner's call.

**Lesson:** every one of these was found by attacking a path I had not yet audited, or by a
check written *after* the code looked finished. A green harness proves only what it asks.

**Paused at:** all findings fixed; suite 182 passed; harnesses parsers 102/102,
features 238/239, visual 90/94, coverage 12/12.
