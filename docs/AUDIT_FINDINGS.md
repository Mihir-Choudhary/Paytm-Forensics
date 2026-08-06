# PaytmForensics — Audit findings register

Opened 2026-07-30. Companion to [`../STATE.md`](../STATE.md).

> **Case figures are redacted.** This repository is public, so monetary amounts appear as
> `₹<amount>` and case timestamps as `<date>` / `<date/time>`. Row counts, ratios and
> percentages are kept because they carry the technical argument without exposing the
> subject's financial activity. Re-run the harnesses in `tools/` against your own
> extraction to reproduce the concrete figures. Merchant names in examples are likewise
> substituted (`FoodCo`, `Quickeats`, `Bistro`, `Acme Foods`); the name-normalisation
> behaviour they illustrate is unchanged. Note `tests/test_m4_gui.py` carried a real
> merchant name from before this audit — left as found, flagged here.

Scope: verification only. **No production code has been changed.** Each finding records
what is wrong, how it was established, what it costs an examiner, and the recommended
fix — but the fix decisions (especially F-01) belong to the project owner.

Status vocabulary:
- **CONFIRMED (executed)** — reproduced by running code; repro script named.
- **CONFIRMED (read)** — provable from the source as written; no execution needed.
- **OPEN** — needs the real extraction; the harness answers it.

---

## F-01 — `immutable=1` silently discards uncheckpointed WAL rows
**Severity: critical** · CONFIRMED (executed) · `paytmforensics/ingest/sqlite_ro.py:17`

`open_ro()` opens every evidence database as:

```python
uri = f"file:{quote(path)}?mode=ro&immutable=1"
```

`immutable=1` tells SQLite the file cannot change, which licenses it to **skip the
`-wal` entirely**. Any row committed to the write-ahead log but not yet checkpointed is
invisible. Android apps like Paytm run Room/SQLite in WAL mode, and a forensic
extraction taken while the app is installed will routinely include a populated `-wal`.

### Evidence
`scratchpad/wal_check2.py` — 1 checkpointed row + 5 WAL-only rows, copied db+wal+shm
the way an extraction would:

```
open_ro (immutable=1)  -> 1 rows: ['CHECKPOINTED']
mode=ro (no immutable) -> 6 rows: ['CHECKPOINTED', 'WAL_ONLY_0' … 'WAL_ONLY_4']
```

`scratchpad/wal_pipeline.py` — the loss propagates through the whole pipeline into the
financial figures:

```
parse_all: {'transactions.passbook': 1}
transactions in case.db: 1  ->  ['CHECKPOINTED']
GROUND TRUTH (db+wal)  : 6 rows, total amount 511.0
REPORTED total amount  : 1.0
artifacts: [('databases/passbook.db','sqlite'), ('databases/passbook.db-wal','other')]
```

### Why it is worse than a plain bug
1. **Silent.** No exception, no warning, no `parse_error` in `audit.log`. `parse_all`
   returns `1` and every downstream consumer treats that as complete.
2. **The `-wal` is seen and hashed but never read.** `artifact.discover` classifies it
   `other`; `integrity.build_manifest` hashes it. So the manifest *proves* WAL data was
   present in evidence the report does not account for — which is precisely the kind of
   discrepancy opposing counsel looks for.
3. **`--verify` cannot catch it.** `integrity.verify_against` compares file hashes, not
   row counts. Both runs are equally incomplete, so verification passes.
4. **It contradicts a stated requirement.** PRD FR-7 explicitly requires parsing
   `androidx.work.workdb` *"(incl. its uncheckpointed WAL)"*. That requirement is
   currently unmet for every database.

### Recommended fix — and the trade-off (owner's call)
Do **not** simply drop `immutable=1`: without it SQLite may create `-shm` and replay the
WAL against the source, breaking EV-1 and `test_D_no_sidecar_written_to_source`.

The defensible route is what the PRD already sanctions in EV-1 — *"operate on a working
copy when any tooling could write"*:

1. Copy `db` + `-wal` + `-shm` to a scratch dir (hash before and after the copy).
2. Open the **copy** with `mode=ro` (no `immutable`) so the WAL is honoured.
3. Record in provenance/audit that the row came via WAL recovery from a verified copy.
4. Emit a loud warning whenever a non-empty `-wal` is present, and report
   `live` vs `wal-recovered` row counts separately so the delta is visible.

Interim mitigation if the copy path is not wanted: at minimum **detect and warn**. A
non-empty `-wal` next to a parsed DB should surface in the report's integrity block.

### Also fix
`artifact.discover` skips `-shm` and `-journal` but not `-wal`, so the WAL is indexed as
a generic artifact. Either handle it or skip it explicitly — indexing it as `other`
implies it was considered when it was not.

---

## F-02 — the completeness tests structurally cannot detect F-01
**Severity: critical (verification gap)** · CONFIRMED (read) · `tests/test_rigorous.py:64`, `tests/synthetic.py:12`

`test_A_passbook_count`, `test_A_chat_message_count`, `test_A_contacts_count`,
`test_A_vpa_cache_count`, `test_A_consent_count` and friends all compare parser output
against `_raw_count()`:

```python
def _raw_count(db, table, where=None):
    ...
    with sql.open_ro(path) as con:          # <-- the SAME blind spot as the parser
        return con.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0]
```

Expected and actual are drawn through the identical WAL-blind reader, so the assertions
hold whether or not data is being dropped. Compounding it, `tests/synthetic.py:_db()`
sets `auto_vacuum` and `secure_delete` but never `journal_mode=WAL`, so no fixture can
exercise WAL behaviour at all.

**Fix:** ground truth must come from an independent reader — a WAL-aware read of a copy,
or counts asserted as literals. Add a WAL-mode fixture to `synthetic.py` with a known
number of uncheckpointed rows and assert they are recovered.

**Wider lesson for this audit:** a green suite here is not evidence of correctness. Every
claim in this register was verified against something other than the project's own tests.

---

## F-03 — the "master chronological timeline" is not chronological in court-facing output
**Severity: high** · CONFIRMED (executed) · `paytmforensics/correlate/timeline.py:14`

`timeline.build()` iterates a `builders` dict domain-by-domain and yields as it goes;
`CaseDB.add_many` inserts in that order; every reader uses `ORDER BY id`. **No sort is
ever applied at build time.**

### Evidence
`scratchpad/report_check.py`, rows scraped from the generated `report.html`:

```
15 Aug 2024, 17:25:56     <- transaction (passbook)
16 Aug 2024, 21:12:36     <- transaction (passbook)
15 Aug 2024, 17:25:56     <- transaction (chat)      back in time
16 Aug 2024, 21:12:36     <- transaction (chat)
19 Aug 2024, 08:31:56     <- location
19 Aug 2024, 08:31:53     <- consent                 back in time
19 Aug 2024, 08:31:53     <- consent
```

Sorting exists **only** in `gui/dashboard.py:131` and `gui/timelineview.py:_events`,
i.e. on screen. `report/html.py:_table` and `report/exporters.py` both take rows in
stored order, so `report.html` and the CSV/JSON export — the artefacts that get
disclosed — present a "chronological timeline" that is actually grouped by domain.

**Fix:** sort in `timeline.build()` (or on read) by `utc_iso`, with a stable tiebreak
such as `(utc_iso, ref_domain, rowid)` to preserve EV-6 reproducibility.

---

## F-04 — timeline omits background jobs, contrary to FR-14
**Severity: high** · CONFIRMED (executed) · `paytmforensics/correlate/timeline.py:16-45`

PRD FR-14 requires merging *"transactions, messages, locations, jobs, sessions,
consents, searches, notifications"*. The `builders` dict has no `job` key, so
WorkManager events never enter the timeline even though they carry a decoded timestamp.

Observed on the synthetic fixture: job has
`last_enqueue.utc_iso = 2024-08-19T08:31:56.881000+00:00`, and
`job events in timeline: 0`.

Also absent: `crash` (`start_time`), `cookie` (`created`), `appstate`, `webcache`.
README further overstates this as *"merges every timestamped record across domains"*.

Note: `message` records with an `amount` are excluded **deliberately and correctly**
(they are represented as transactions; see `timeline.py:52`) — that one is not a defect.

**Fix:** add `job`/`crash`/`appstate` builders, or narrow the README and PRD claim to
the domains actually covered. Behaviour and documentation must agree either way.

---

## F-05 — entity resolution is order-dependent; the same input yields different entities
**Severity: high** · CONFIRMED (executed) · `paytmforensics/correlate/entities.py:78-96`

The comment says *"union-find style merge on shared keys"*, but the loop breaks on the
first match and never revisits earlier entities:

```python
for e in ents:
    hit = None
    for m in merged:
        if e.keys & m.keys:
            hit = m
            break          # <-- no transitive closure, no re-check
```

Given a transitive chain A—phone—B—vpa—C with no direct A–C link, the result depends on
row order.

### Evidence
`scratchpad/entity_check.py`, same three person records both times:

```
order_ABC: 1 entities
   names=['Rec A','Rec B','Rec C'] phones=['9000000001'] vpas=['x@ptybl']
order_ACB: 2 entities
   names=['Rec A','Rec B']         phones=['9000000001'] vpas=['x@ptybl']
   names=['Rec C']                 phones=[]             vpas=['x@ptybl']
```

### Impact
- **A counterparty can be double-counted** as two entities — directly undermining FR-13
  and the "unified counterparty" claim in the README.
- **The same VPA lands in two entities.** `entities.build` then does `by_vpa[v] = e`, so
  last-writer-wins and that counterparty's transactions are attributed to whichever
  fragment happened to be built last. `txn_count`, `total_received`, `total_paid` — the
  numbers on the dashboard's "Top counterparties" — are therefore split arbitrarily.
- Row order comes from `iter_domain`'s `ORDER BY id`, i.e. parser registration order.
  Stable per version, but it means correctness is accidental, not designed.

`test_I_no_duplicate_customer_ids_across_entities` inspects **only** `customer_ids`, so a
split on phone or VPA passes.

**Fix:** real union-find (or iterate to a fixed point), then extend the duplicate test to
`phones`, `vpas` and `sendbird_ids`. Harness section 8 checks all four.

### Related gap
Transaction→entity attribution keys on `counterparty_vpa` then `counterparty_name` only.
`counterparty_mobile` is parsed (`transactions.py:54`, from `searchableStrings`) and
persons carry `phone`, but the mobile is **never used as a join key** — so P2P passbook
transactions that only identify the counterparty by phone go unattributed. Under-counts
`txn_count` for exactly the person-to-person payments an investigator cares most about.

---

## F-06 — correctly parsed fields never reach the report
**Severity: high** · CONFIRMED (executed) · `paytmforensics/gui/datasource.py:13`, `paytmforensics/report/html.py:20`

`report/html.py:_table` renders `ds.columns(domain)`, which returns `DISPLAY_COLUMNS` —
a curated subset. Anything outside it exists in `case.db` and in the CSV/JSON export, but
never appears in the HTML report or the GUI grid (only in the per-record detail pane).

### Evidence
`scratchpad/report_check.py` — synthetic passbook row has `errorCode=1103`:

```
txn with error: PTMBBB222 code=1103 msg='You have entered incorrect passcode. Kindly retry.'
error_message value in report?       False
'Contacts (raw)' / person section?   False
```

The error-code decoding works perfectly — `error_mapper.json` resolves 1103 correctly —
and the result is then thrown away before the human sees it.

### Not displayed (confirmed on the synthetic run; harness section 6 does this per domain)
| Domain | Populated but not shown |
|---|---|
| `transaction` | `error_code`, `error_message`, `txn_id`, `source_txn_id`, `payment_mode`, `status_raw`, `category_raw`, `tag`, `note`, `account_branch` |
| `person` | **whole domain absent from `REPORT_DOMAINS`** — plus `customer_id`, `sendbird_id`, `masked_account`, `verified_name`, `country_code` |
| `message` | `sender_id`, `status`, `channel_url`, `encrypted_blob_present` |
| `entity` | `customer_ids`, `sendbird_ids`, `account_age_text`, `source_files` |
| `location` | `speed` |
| `diagnostic` | `error_code`, `error_message`, `session_id`, `customer_id`, `device_id`, `latitude`, `longitude`, `is_rooted`, `screen_name` |

`error_message` on failed transactions and `encrypted_blob_present` on messages are the
most consequential: the first explains *why* a payment failed, the second is FR-4's
explicit requirement to *"flag encrypted message BLOBs as present-but-unreadable"* — it
is flagged in the data model and then not surfaced.

`person` being absent from the report while `entity` is present is defensible as an
editorial choice (aggregate over raw), but it drops `masked_account` and `verified_name`,
which appear nowhere else in the report. FR-3 expects them.

**Fix:** either extend `DISPLAY_COLUMNS`/`REPORT_DOMAINS`, or give the report a
"complete field dump" mode. Exports are already lossless, so this is presentation-layer
only — but the report is the court-facing artefact.

---

## F-07 — METHODOLOGY overstates what the report surfaces
**Severity: high (documentation vs implementation)** · CONFIRMED (executed) · `METHODOLOGY.md:15`, `paytmforensics/report/html.py:118`

METHODOLOGY §2 states:

> Every emitted record carries: `source_file`, `source_table`, `rowid` (or `byte_offset`
> for carved data), `origin`, `confidence`, and the source file's `ingest_sha256`.
> **Reports and exports surface these fields.**

The record model does carry all six, and the **CSV export does** surface all six
(`exporters.py:29`). The **HTML report does not** — `_table` emits only `source_file`,
`source_table` and `origin`:

```
report mentions rowid?                 False
report mentions byte_offset?           False
report shows per-record confidence?    False
report shows ingest_sha256 per record? False
```

So a record in the report cannot be traced to its exact row, and a carved record's
confidence is invisible unless it happens to sit in `DISPLAY_COLUMNS["carved"]`. Since
this is the document offered to support admissibility, the mismatch matters more than the
missing column does.

**Fix:** add the three columns to the report's provenance cell, or amend METHODOLOGY §2
to say exports surface them and the report surfaces a subset. Do not leave the stronger
claim standing.

---

## F-08 — date-range filtering silently returns zero rows on some grids
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/gui/filters.py:8-17`

`record_utc` looks for `utc_iso`, `timestamp`, `last_enqueue` — and nothing else. But
`cookie` records store time in `created`/`expires` and `crash` in `start_time`. For those
domains `record_utc` returns `None`, and `FilterSpec.matches` treats `None` as "does not
match":

```python
utc = record_utc(rec)
if utc is None:
    return False
```

### Evidence
Three records all inside `<date> .. <date>`:

```
cookie   record_utc=None                         passes filter=False
crash    record_utc=None                         passes filter=False
txn      record_utc=<date/time>+00:00    passes filter=True
rows surviving the date filter: 1   (of 3 in-range records)
```

FR-G3 requires date/time filtering on **every** data grid. The failure mode is the
dangerous one: an examiner narrows to a date window, the cookie grid empties, and the
natural reading is "no browser activity in that window" when the records exist and are
in range.

**Fix:** add `created` and `start_time` to `record_utc`'s key list (`expires` should stay
out — it is a future expiry, not an event time). Consider making a missing timestamp
distinguishable from "outside range" in the UI.

---

## F-09 — README misdescribes the `message` domain; payments appear in two domains
**Severity: medium** · CONFIRMED (read) · `README.md:158`, `paytmforensics/parsers/chats.py:22`

README: `| message | Chat DB (Sendbird), non-payment messages |`

`ChatParser.parse` iterates **all** of `ChatMessageEntity` with no payment filter, and
deliberately extracts `amount` and `rrn` onto each `Message`. So a chat payment that is
absent from the passbook is emitted **twice**: once as `transaction` (by
`ChatLedgerParser`) and once as `message`.

That is a defensible design — `test_audit_fixes.py::test_chat_payments_still_present_as_messages`
asserts it on purpose, and `timeline.build` correctly de-duplicates by skipping messages
with an `amount`. The defect is that the README describes different behaviour, so anyone
summing `transaction` + `message` counts will double-count payments.

**Fix:** README should read "all chat messages, including payment messages (which are
also emitted as transactions; the timeline de-duplicates)".

---

## F-10 — carve dedup ignores message-domain RRNs, risking false "deleted" labels
**Severity: medium** · CONFIRMED (read) · `paytmforensics/carving/carve_runner.py:20-36`

`_live_values` builds the live identifier set from the `transaction` and `person` domains
only. Chat-payment RRNs that live in the **`message`** domain are not collected.

The carver only reads unallocated regions, so a live row's bytes are not normally there —
**but SQLite `UPDATE` frees the old cell**, and chat/message rows are updated constantly
(delivery state, read receipts). A stale copy of a still-live RRN is very likely to sit in
a freeblock. When found, it is not in `live["rrns"]`, so it is emitted as a **new carved
identifier** — i.e. presented as recovered deleted data when the record is live.

This bears directly on EV-8 ("carved records … never presented as confirmed live data" —
the inverse error is just as damaging) and on the README's claim that carving "roughly
doubles the recoverable transaction and counterparty records", which may be partly
counting live values back.

**Fix:** include `message` RRNs (and `message`/`entity` phones and VPAs) in
`_live_values`. Harness sections 2 and 8 give the numbers needed to size the effect once
real data is available.

### Secondary carving notes (lower confidence, verify on real data)
- `seen_sig` is scoped per database, so the same identifier carved from two DBs yields two
  records — carved counts are "unique identifiers per DB", not records.
- `_RRN_B = rb"(?<!\d)\d{12}(?!\d)"` matches any isolated 12-digit run. The lookarounds
  correctly exclude 13-digit ms timestamps, but binary data will still produce hits.
  Confidence is labelled 0.45–0.5, which is honest; the false-positive **rate** on real
  data is still unmeasured.
- Overflow pages are not identified; one whose first byte happens to be `0x0D` would be
  walked as a leaf page. Low probability, bounded by the identifier validators.

---

## F-11 — the HTML report renders every row by default
**Severity: medium** · CONFIRMED (read) · `paytmforensics/report/html.py:210`, `paytmforensics/cli.py:66`

`_table(ds, dom, limit=table_limit)` with `shown = rows[:limit] if limit else rows`, and
`cli.py` calls `htmlrep.generate(args.out, rp, fmt="html")` — no `table_limit`. So every
`config`, `pref`, `diagnostic` and `carved` row is inlined into a single self-contained
HTML file. NFR-2 anticipates 100k+ row grids; the report has no pagination, no streaming
and no size guard, and the `_charts` pass plus 20 full tables are all built in memory as a
list of strings.

**Fix:** default the CLI to a sane `table_limit` (the "… N more rows (see JSON/CSV
export)" footer already exists and is the right UX), and let `--full-report` opt in.

---

## F-12 — recent-search parser discards the label it computes
**Severity: low** · CONFIRMED (read) · `paytmforensics/parsers/search_state.py:26-40`

```python
query = r.get("id")
item = r.get("item")
if item:
    try:
        query = json.loads(item).get("cta", {}).get("label", query) or query
    except (ValueError, TypeError):
        pass
yield SearchQuery(
    ...
    query=r.get("id"),        # <-- `query` computed above is never used
```

The friendly CTA label is parsed and thrown away; the raw `id` is emitted instead. Either
the enrichment is dead code that should be deleted, or `query=query` was intended.
Also `raw` keeps only `id`/`vertical_id`, dropping `item`, so the label is not recoverable
downstream. Worth confirming against the real schema whether `recent_Search_Tbl` even has
a `url` column — if not, `url` is always `None`.

---

## F-13 — stale comment contradicts the code directly above it
**Severity: low** · CONFIRMED (read) · `paytmforensics/parsers/transactions.py:217-220`

```python
# NOTE: chat payment events are intentionally NOT emitted as `transaction` records.
# They are captured by the chat parser as `message` records … which avoids
# double-counting … and keeps the `transaction` domain equal to the authoritative
# passbook ledger. See chats.py.
```

`ChatLedgerParser` (same file, line 78) does exactly the opposite: it emits chat payments
as `transaction` records, deduped by `uniqueKey`. The comment describes a superseded
design and will mislead the next reader about which domain is authoritative. Delete it.

---

## F-14 — subject's account age is never populated
**Severity: low** · CONFIRMED (read) · `paytmforensics/parsers/identity.py:38,71`

`account_age = None` is initialised and never assigned, then passed as
`account_age_text=account_age`. FR-1 lists "account-age text" as a required subject
field. `contacts.py:_account_age` already implements the extraction (from
`getInfoSync.jsonString.customerCreationText`) for counterparties — the subject row just
never calls it.

**Fix:** reuse `contacts._account_age` on the `isMe=1` row.

---

## F-15 — the two text-search paths search different fields
**Severity: low** · CONFIRMED (read) · `paytmforensics/gui/datasource.py:117`, `paytmforensics/gui/filters.py:51`

`DataSource.global_search` says it matches *"the per-table filter behaviour"* and
excludes `provenance`/`raw`/`domain`. `FilterSpec.matches` calls `_all_strings(rec)` on
the **whole** record, including `provenance` and `raw`. So the same query gives different
results in the top-bar search versus a grid filter — e.g. a grid filter matches on a
source filename or an ingest hash, global search does not.

`test_global_search_matches_values_not_keys` covers global search only. Pick one
behaviour, apply it in both places, and fix the docstring.

---

## OPEN — requires the real extraction

The harness (`scratchpad/audit_extraction.py`) answers all of these in one run.

| # | Question | Why it matters | Harness §|
|---|---|---|---|
| O-1 | Which `statusKey` / `txnIndicator` / `txnCategory` / `WorkSpec.state` values appear? | `settled = (statusKey == "2")` (`transactions.py:70`). **Any unmapped success code ⇒ `settled=False` ⇒ that payment is excluded from every money total** on the dashboard and in the report charts. `STATUS_KEY` is documented as "conservative" guesswork for 1/3/4; `TXN_CATEGORY` has only 3 entries, so the "Spend by category" chart may be mostly `code:N`. | 3 |
| O-2 | How often is the 12-digit RRN *not* the last `searchableStrings` token? | `_searchable_rrn` takes the last comma token only. Every miss is a silently absent NPCI reference — the single most important identifier in a UPI investigation. | 5 |
| O-3 | Which tables hold rows but produce no records? | PRD G1 claims 100% of plaintext artifacts parsed. Reconciles every table in every DB. | 2 |
| O-4 | Any `epoch_type` of `unknown` / `invalid` / `out_of_range`, or implausible dates? | Detection is by magnitude (`timestamps.py:_interpret`); a misdetected column silently yields a wrong date. | 4 |
| O-5 | Did any parser raise and get swallowed? | `Case.parse_all` catches everything into `results[name] = -1`; the CLI prints it as text among the counts. **Check this first on every real run.** | 1 |
| O-6 | Is `counterparty_vpa` (from `UthListingEntity.identifier`) always actually a VPA? | It is populated unconditionally; if `identifier` is sometimes a phone or merchant id, the column is mislabelled and entity joins mis-key. | 2/8 |
| O-7 | How many chat transactions have no `uniqueKey`? | Dedup is `if uniq and uniq in pb_ids`. A payment with no `uniqueKey` is never deduped ⇒ double-counted against the passbook. | 2 |
| O-8 | Do token-shaped values survive into output? | `prefs.REDACT` is an exact-match set of 7 key names across a curated 7-file list. Any other token-bearing key in any other prefs file is emitted verbatim. | 9 |
| O-10 | What `msgStatus`/`r_sts` spellings appear on chat payments? | `transactions.py:138` sets `settled = (status == "SUCCESS")` by **exact string match**. Any other spelling ⇒ every chat payment is `settled=False` ⇒ excluded from all money totals. Same failure class as O-1, different code path. | 3 |
| O-11 | Does the subject's `sendbirdUserId` actually join to `ChatMessageEntity.senderId`? | `transactions.py:134` derives direction from that equality. If the id spaces don't join (or no `isMe=1` row exists), **every chat transaction is labelled `credit`** — systematic money-in inflation with no error raised. | 3 |
| O-9 | Carver false-positive rate; how much of the "≈2× recovery" claim is live data? | See F-10. | 2/8 |

### Additional smaller items to check against real data
- `location.py:CookieLocationParser` emits **one** fix for the whole cookie store, taking
  the last `lat` row's `creation_utc` and pairing it with whatever `long` row was seen
  last — potentially from a different host. If several hosts carry `lat`/`long`, fixes are
  conflated or lost.
- `notifications.py` gives `PushData` rows a `timestamp` derived from `expiry` — a
  **future** time — displayed in the `notification` grid's `timestamp` column. The
  timeline excludes them (by matching the literal string `"(push dedup record)"`, itself
  fragile), but the grid shows an expiry as if it were an event time.
- `sqlite_ro.open_ro` sets `text_factory` to decode with `errors="replace"`, silently
  substituting U+FFFD. Given NFR-7 and the encoding test `test_G_rupee_and_emoji_preserved`,
  replacement should be *counted and reported*, not silent.
- `CaseDB._init_schema` runs `DELETE FROM records` on **every** construction. Documented as
  idempotency, but it means constructing a `Case` against an existing case directory
  destroys it — including in a "just re-verify" workflow. A guard or explicit
  `--overwrite` would be safer.
- Most parsers subset `raw` to a handful of columns, while `models.py:40` documents `raw`
  as *"original column values, untouched"*. `config_diag._DiagBase` in particular drops the
  entire `event_data` JSON it parsed. Either widen `raw` or soften the docstring.

---
---

# Session 2 addendum — results against the real extraction (2026-07-30)

> **Contains case-specific figures** (amounts, dates, row counts) derived from
> a real device extraction. No names, phone numbers,
> customer IDs, VPAs or RRNs are recorded here. Review before sharing outside the case.

Extraction: 698 files, 38 MB, **18 databases with a non-empty `-wal`**.
Full harness output: `tools/audit_extraction.py` (re-runnable); the run used for these
figures is preserved at `scratchpad/real/audit.txt` (session scratch).

## F-01 is LIVE on this extraction, and it is the dominant problem

Corrected reconciliation (the first pass enumerated tables from the *immutable* view and
therefore never compared tables whose **schema itself** lives in the WAL — a flaw in the
harness, since fixed):

| database :: table | tool sees | actually present | lost |
|---|---:|---:|---:|
| `bank_app_manager_database :: bankAppManagerTable` | 0 | 300 | **300** |
| `passbook.db :: UthListingEntity` | 44 | 58 | **14** |
| `passbook.db :: UthInstrumentEntity` | 44 | 58 | 14 |
| `bank_signal :: SignalEventDb` | 0 | 24 | **24** |
| `paytmbank_error_analytics :: PBHawkEyeEvent` | 211 | 230 | 19 |
| `ups_database :: ConsentTable` | 0 | 4 | **4** |
| `cache_database :: cache_table` | 5 | 7 | 2 |
| `search_db :: recent_Search_Tbl` | 0 | 1 | **1** |
| `paytm_error_analytics :: Event` | 0 | 1 | 1 |
| `androidx.work.workdb :: SystemIdInfo` | 6 | 7 | 1 |

The aggregate "96.2% of rows visible" is **misleading and should not be quoted** — it is
dominated by `appManagerDB`'s 7,011 intact config rows. What matters is per-domain, where
four domains are at **0%**:

- **Location history (FR-5): 24 GPS fixes → 0.** `bank_signal`'s table does not exist in
  the immutable view at all.
- **Consents (FR-6): 4 → 0.** `report.html` therefore has **no Consents section**. An
  examiner reads that as "no consents granted".
- **Search history (FR-9): 1 → 0.** Same — **no Searches section** in the report.
- **Bank config (FR-10): 300 → 0.**

### The transaction ledger loss is the worst of it
The 14 invisible passbook transactions are **all the most recent**:

```
tool-visible date range : <start>  ..  <cutoff>
actual date range       : <start>  ..  <cutoff + ~8 weeks>
```

The tool's ledger stopped roughly **two months before the newest transaction actually
present**, so every transaction in that window was missing — including the **single
largest transaction in the entire data set**, which was ~2.4x the next biggest.

```
settled money movement, actual : 100%
settled money movement, tool   : 69.2%
UNREPORTED                     : 30.8% of all settled value
```

An investigator working from this output would conclude the account went quiet at the end
of May. It did not.

## F-16 (NEW) — deleted rows are reported as `origin=live`
**Severity: high** · CONFIRMED (executed)

The inverse of F-01. Where a WAL commit *deleted* rows, the immutable view still shows the
pre-delete state, and the tool labels those rows `origin=live`:

| database :: table | tool reports live | actually present | |
|---|---:|---:|---|
| `pai_signal :: SignalEventDb` | 71 | 0 | **71 deleted rows presented as live** |
| `PaytmMessageDatabase :: NotificationData` | 1 | 0 | |
| `RealtimeSmsUploadDb :: RealtimeSmsUploadTable` | 1 | 0 | |
| `pai_push_signal :: SignalEventDb` | 1 | 0 | |

This is a distinct evidentiary problem from F-01 and arguably harder to defend: the tool
asserts `origin=live, confidence=1.0` for records the application had deleted. EV-8's
concern ("carved records never presented as confirmed live data") has an unaddressed
mirror image. Note `pai_signal.SignalEventDb` also has **no parser** at all, so those 71
rows are simultaneously mislabelled and unparsed.

## F-17 (NEW) — `txnCategory=2` is mislabelled "cashback"
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/enrich/enums.py:18`

Checked the inferred label against the app's own `txnTag`:

| `txnCategory` | app's `txnTag` | rows | tool label | correct? |
|---|---|---:|---|---|
| 1 | `🥘 Food` | 39 | `food_and_beverages` | **yes** |
| 1 | *(null)* | 1 | `food_and_beverages` | unverifiable |
| 2 | `💵 Money Received` | 3 | `cashback` | **no** |
| 2 | `💰 Cashback` | 1 | `cashback` | yes |

`TXN_CATEGORY[1]` is vindicated. `TXN_CATEGORY[2]` is wrong for 3 of 4 rows — category 2
is broader than "cashback" (it covers incoming transfers).

The sharp edge: **`txnTag` is the app's own authoritative human label, and F-06 means it
is parsed but never displayed.** So the report shows an inferred, partly-wrong category and
hides the correct one sitting in the same row.

## F-18 (NEW) — ₹<amount> of completed UPI requests counted in neither direction
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/parsers/transactions.py:131-138`

Chat transaction breakdown as stored:

| status_label | direction | settled | count |
|---|---|---|---:|
| SUCCESS | credit | True | 19 |
| SUCCESS | debit | True | 13 |
| **COMPLETED** | **None** | **False** | **7** |
| FAILURE | debit | False | 2 |
| DECLINED | debit | False | 1 |

`is_request = "REQUEST" in ctype and "RESPONSE" not in ctype` short-circuits `direction`
to `None` for `customType=UPI_REQUEST`, and `settled` requires `status == "SUCCESS"` — so
`COMPLETED` never settles. But all 7 carry **both an RRN and a uniqueKey**, which means
money moved and NPCI referenced it.

Result: **7 real payments sit in no total** — not "Received", not "Paid" — and the
Transactions grid shows a blank direction for them. Together they were ~19% of the value
the dashboard did report as Paid, invisible to both figures.

`COMPLETED` should be treated as settled, and a completed request's direction inferred
from who raised it.

## F-19 (NEW) — 211 GPS fixes exist in the case and appear in no table
**Severity: high** · CONFIRMED (executed)

The most consequential instance of F-06:

```
records in the 'location' domain (the Location grid)   : 1      (a cookie fix, <date>)
diagnostic records carrying lat/lon                     : 211    (<date> .. <date>)
```

`DISPLAY_COLUMNS["diagnostic"]` has no `latitude`/`longitude`; confirmed against the
generated report — the Diagnostics table's columns are
`timestamp, event_type, message, flow_name, network_type, network_carrier, battery_pct,
process_state, app_version`. The 211 coordinate pairs are used by `mapview._fixes` and by
the KML/GeoJSON export, but appear in **no table and nowhere in `report.html`**.

So the report's Location section reads "Location (1)" — a single April cookie — while the
case holds 211 GPS fixes across two months. Combine with F-01's loss of `bank_signal`'s 24
fixes and the location picture in the court-facing report is close to worthless.

## Re-confirmed on real data

- **F-03** — timeline: 299 events, **48 out-of-order pairs** as stored and rendered.
- **F-04** — timeline domains are only `transaction, location, notification, diagnostic`.
  Absent: `job` (9 records), `crash`, `cookie`, `appstate`, `webcache`. `message`
  contributes 0, which here is *correct* — all 84 chat messages carry an amount and are
  represented as transactions.
- **F-06/F-07** — no `rowid` / `byte_offset` per record in the report; `person` domain
  (86 records) absent from it; the one decoded diagnostic `error_message` in `case.db` does
  not reach the report. (Note: a naive grep for `error_message` in the report *does* hit —
  but only on `appManagerDB` config **key names**, not decoded messages. Verified.)
- **F-11** — `report.html` is **2.57 MB**, with all 7,027 config rows inlined.
- **F-14** — subject's `account_age_text` is empty, as predicted. `bank_name` also empty.
- **O-8** — **2 FCM push tokens in plaintext** (`{"token":"…APA91b…"}`) from
  `com.google.android.gms.appid.xml`; only 2 of 129 pref values were redacted. A config
  value named `MinKycOTPClientSecret` is also emitted verbatim. `prefs.REDACT` is an
  exact-match key list and these keys are base64 blobs, so it cannot match them.
- **PushData expiry-as-timestamp** — the `notification` domain's newest timestamp is
  **<date>**, a week in the future, because `expiry` is used as the event time.

## Verified CORRECT — no defect found

Recording these so the register is not read as uniformly negative:

- **No parser raised.** `audit.log` has no `parse_error`.
- **`statusKey` fully mapped** — only values 2 (43 rows) and 1 (1 row) occur, both in
  `STATUS_KEY`. The unmapped-success-code risk (O-1) does **not** bite here; money totals
  are safe from that particular failure.
- **RRN extraction is perfect: 44/44** rows have the 12-digit RRN as the last
  `searchableStrings` token. The fragile-looking assumption in `_searchable_rrn` holds
  completely on this data (O-2 closed).
- **Chat direction logic fires correctly** — exactly one `isMe=1` row, and its
  `sendbirdUserId` appears as `senderId` on 56 of 84 messages (O-11 closed).
- **Entity resolution held.** 82 entities, **zero** identifier collisions across
  `customer_ids`/`phones`/`vpas`/`sendbird_ids` — F-05's order-dependence did **not**
  manifest on this input. It remains a latent defect, not an active one here.
- **Attribution: 85 of 86** transactions attributed. The mobile-not-a-join-key gap noted
  under F-05 is latent too — 42 transactions lack a VPA and 25 of those carry a mobile,
  but name matching caught them anyway.
- **Subject correctly identified**, exactly one, with name / customer_id / phone /
  country_code / sendbird_id all populated.
- **Timestamps** decode cleanly: `unix_ms` and `webkit_us` throughout, no misdetection.
  The only undecoded values are 1 `crash.start_time` and 2 `cookie.expires` (session
  cookies with a zero expiry — benign).
- **`txnCategory=1 → food_and_beverages` is right** (39/39 corroborated by `txnTag`).
- **`txnIndicator`** — only 1/2 occur, both mapped.

## Carving recovered **0** records — and the README claim is not met

```
chatDb.db       327 pages   freelist=0   231 unallocated regions   241,872 bytes
passbook.db      30 pages   freelist=0    28 regions                54,789 bytes
cache_database    7 pages   freelist=0     9 regions                23,094 bytes
contacts          1 page    freelist=0     1 region                  3,988 bytes
```

Free/slack space exists, but no region held a reconstructable record carrying a
VPA/RRN/phone/txn-id, and the pattern pass found nothing either. Reporting 0 is
**honest** and exactly what METHODOLOGY §8 promises. But it directly contradicts:

- README: carving *"roughly doubles the recoverable transaction and counterparty records"*
- PRD §12: *"Carving recovers ≥ the counts observed in manual analysis (≈2× live
  transactions/counterparties)"*

Those claims should be softened to "recovery depends on source state; may be zero".

A likely reason, and a real gap: **the carver only reads the main database file.** With 18
populated WALs, recently deleted rows are most likely *in the WAL*, which
`SqliteCarver.__init__` never opens. Fixing F-01 by parsing a db+wal copy would probably
also make carving productive — the two findings share a root cause.

Side note: F-10 (carve dedup ignoring message-domain RRNs) could not bite here because
zero records were carved. It stays open as a latent issue.

## Tables with rows but no parser (real data)

13,358 rows total, but the headline number is noise — **13,006 of them are
`AppLocale.db :: english`**, app UI localisation strings with no evidentiary value. The
ones that matter:

| rows | table | why it matters |
|---:|---|---|
| 160 | `chatDb.db :: DBChannelUserEntryCrossRef` | read as a join input; no records of its own is fine |
| 71 | `pai_signal :: SignalEventDb` | **no parser**; same `SignalEventDb` shape the location parser reads from `bank_signal`. Also see F-16 (WAL says deleted) |
| 41 | `chatDb.db :: TBL_CHANNELS` | **never read.** FR-4 requires reconstructing conversations *"from `TBL_CHANNELS` + `ChatMessageEntity` + `DBChannelUserEntryCrossRef`, ordered via `TBL_MESSAGE_HISTORY`"* — 41 conversations' metadata is unused |
| 17 / 9 | `workdb :: WorkTag` / `WorkName` | would give human-readable job names instead of raw worker classes |
| 11 | `chatDb.db :: TBL_MESSAGE_HISTORY` | FR-4's stated ordering source; unused |
| 1 | `RealtimeSmsUploadDb :: RealtimeSmsUploadTable` | SMS upload evidence; unparsed (and per F-16, deleted) |

---
---

# Session 3 addendum — per-feature rigorous audit (2026-07-31)

Three independent harnesses, all re-runnable:

| tool | scope | result |
|---|---|---|
| `tools/audit_features.py` | filters, pure functions, datasource, exports, integrity, CLI, GUI runtime | **213 checks — 196 pass, 7 fail, 10 warn** |
| `tools/audit_parsers.py` | every parser + view, values vs independent SQL | **98 checks — 82 pass, 11 fail, 5 warn** |
| `tools/audit_extraction.py` | source-vs-case reconciliation (session 2) | 10 sections |

`PySide6-Essentials` was installed this session, so **the GUI was exercised at runtime
offscreen** for the first time. QtWebEngine is still absent, so the map's Leaflet path is
untested and the Qt PDF backend is unavailable; the offline fallback path *was* tested.

Two defects in my own audit code were found and fixed rather than reported as product
bugs: a timestamp expectation that was simply wrong (`decode(1723742756.754)` → `unix_s`
is **correct**, since `int()` truncates to 10 digits), and an unconditional note string.

## F-20 — `date_to` excludes the whole final day
**Severity: high** · CONFIRMED (executed) · `paytmforensics/gui/filters.py:60-63`

Dates are compared as **strings** against full ISO timestamps:

```python
if self.date_to and utc > self.date_to:   return False
```

A record at `<date/time>.930000+00:00` versus `date_to="<date>"` compares
greater, so it is rejected — even though `FilterSpec`'s own docstring says
`date_to  # ISO; inclusive`.

```
date_to='<date>'                        -> False   (record IS on 12 May)
date_to='<date>'                        -> True
range <date>..<date>                -> False
range <date>..<date>                -> True
```

Every date range an examiner types **silently loses its last day**. Combined with F-08
(cookie/crash grids return nothing at all) the date filter is the least trustworthy
control in the GUI.

**Fix:** normalise both sides to a comparable instant — if `date_to` has no time part,
treat it as end-of-day (`T23:59:59.999999`).

## F-21 — an unpadded date silently excludes almost everything
**Severity: high** · CONFIRMED (executed) · same code path

`date_from="2026-5-1"` — which a user will type — lexically exceeds `"<date>"`
because `'0' < '5'` at position 5. Result: `False`. The filter appears applied and
returns almost nothing, with no error and no hint that the format was wrong.

**Fix:** parse the input to a date before comparing, and reject/flag unparseable input
instead of comparing raw strings.

## F-22 — amount and direction filters silently empty unrelated grids
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/gui/filters.py:64-72`

`matches` reads `rec.get("amount")` and `rec.get("direction")` unconditionally on every
domain. Set `amount_min=0` while viewing config, prefs, cookies or capabilities and the
grid empties — the field simply does not exist on those records.

Structurally identical to F-08, and the same failure mode: a wrong **empty** result that
reads as "no matching evidence". Because the filter card is shared across every grid, the
controls are always visible even where they cannot apply.

**Fix:** skip a criterion when the field is absent from the record (or grey out
inapplicable controls per domain).

## F-23 — `field_equals` matches absent fields against the string `"None"`
**Severity: low** · CONFIRMED (executed) · `paytmforensics/gui/filters.py:79-81`

`if str(rec.get(k)) != str(v)` — so `field_equals={"nosuchfield": "None"}` **matches**
every record, and `settled=True` disagrees with `settled="true"` (`str(True) == "True"`).
Stringly-typed comparison; not currently reachable from the GUI, but it is public API.

## F-24 — FR-G3 "save filter presets per case" is not implemented
**Severity: medium (unimplemented requirement)** · CONFIRMED (read) · `paytmforensics/gui/app.py`

PRD FR-G3 ends: *"Combine filters (AND) and **save filter presets per case**."*
AND-composition works (verified, two- and three-field specs narrow correctly). There is no
preset save/load anywhere in `gui/app.py` — no persistence, no UI. Not a bug; a missing
feature that the PRD lists as part of "a primary requirement".

## F-25 — the CLI accepts a nonexistent evidence path and exits 0
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/cli.py`

```
python -m paytmforensics.cli --extraction /does/not/exist --out /tmp/o
  -> rc=0, "0 files hashed -> manifest.json", a complete empty case, no error
```

`os.walk` on a missing directory yields nothing, so the run "succeeds" and produces
`case.db`, `manifest.json`, `audit.log` and `case_meta.json` describing an empty
extraction. A typo'd path is indistinguishable from an extraction with no parseable data.

**Fix:** validate `--extraction` is an existing directory and exit non-zero otherwise.

## F-26 — passbook-only counterparties get no entity at all (FR-13)
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/correlate/entities.py:47`

`_merge_people` iterates **only** `case.iter_domain("person")`, and `person` records come
solely from `chatDb.TBL_USERS` and `cache_database.cache_table`. A counterparty that
appears *only* in the passbook is therefore never an entity, and
`entities.build`'s transaction loop finds no target for it.

Measured on this extraction:

```
distinct counterparty VPAs in transactions   : 7
  present in some person record              : 5
  not in any person record                   : 2
    rescued by counterparty_name match       : 1
    left with NO entity                      : 1     (settled Rs 4.00)
```

That surfaced as a reconciliation gap: entity received-total **₹<amount>** vs settled
credits **₹<amount>**. Small here only because this device's activity is chat-heavy. On a
merchant-payment-heavy device most counterparties would exist only in the passbook, and the
dashboard's counterparty aggregation would omit most of the money.

**Fix:** seed entities from transaction counterparties too (VPA / name / mobile), not just
from `person`. This is the same root as the mobile-not-a-join-key gap noted under F-05.

## F-27 — no audit-log verifier is shipped (EV-5)
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/core/audit.py`

`AuditLog` can only *append*. There is no verification function anywhere in the package,
so a third party cannot check the chain without writing their own.

I wrote one and ran it: the real 33-entry `audit.log` **verifies end to end, 0 breaks**,
and an edited entry *is* detectable (26 downstream mismatches after tampering with one
`parse` record). So the mechanism is sound — it is simply not exposed.

**Fix:** ship `AuditLog.verify(path)` and a `--verify-audit` CLI flag.

## F-28 — 8 notifications are timestamped in the future
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/parsers/notifications.py:41`

`PushData` rows get `timestamp=decode(r.get("expiry"))` — an expiry, not an event time. On
this extraction 8 of 9 notifications carry timestamps **after the extraction date**
(newest <date>). They sit in the `notification` grid's `timestamp` column looking like
event times.

The timeline correctly excludes them, but by matching the literal string
`"(push dedup record)"` (`timeline.py:57`) — a fragile coupling to display text.

**Fix:** put expiry in its own field, leave `timestamp` null for dedup rows, and switch the
timeline exclusion to a structural flag.

## F-29 — invalid filter input is silently discarded
**Severity: low** · CONFIRMED (executed) · `paytmforensics/gui/app.py:292-296`

`_current_spec.fnum` catches `ValueError` and returns `None`. Typing `abc` in the min/max
amount box means "no amount filter" — the row count does not change, so the examiner
believes a filter is active when it is not. Verified: grid stayed at 86 rows.

## F-30 — export target goes stale after visiting the Dashboard
**Severity: low** · CONFIRMED (executed) · `paytmforensics/gui/app.py:221-225`

`_on_nav` returns early for `dashboard` without clearing `self._model`. So: open
Transactions → click Dashboard → click JSON, and you export the transactions grid while
looking at the Dashboard. Verified `_model.domain` is still `transaction`.
(The `map` branch does clear it correctly.)

## F-31 — CSV timestamps are JSON blobs, not sortable values
**Severity: low** · CONFIRMED (executed) · `paytmforensics/report/exporters.py:13`

`_flatten_cell` JSON-dumps any dict, so the `timestamp` column contains
`{"epoch_type": "unix_ms", "raw": 1723742756754, "utc_iso": "..."}` in a single cell.
Faithful and lossless, but the CSV cannot be sorted or filtered by date in a spreadsheet —
the main reason an analyst asks for CSV.

**Fix:** also emit flattened `timestamp.utc_iso` / `timestamp.raw` / `timestamp.epoch_type`
columns.

## F-32 — TBL_CHANNELS and TBL_MESSAGE_HISTORY are never parsed (FR-4)
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/parsers/chats.py`

FR-4: *"Reconstruct conversations from `TBL_CHANNELS` + `ChatMessageEntity` +
`DBChannelUserEntryCrossRef`, ordered via `TBL_MESSAGE_HISTORY`."*

`TBL_CHANNELS` (41 rows) and `TBL_MESSAGE_HISTORY` (11 rows) are read by nothing.
Conversations are inferred from messages alone, so **41 channels exist but only 11 appear**
in the chat view — 30 conversations with no surviving messages are invisible, along with
their participants and metadata. For an investigator, "a conversation existed with X" is
itself evidence.

## Verified working — the substantial pass list

**Passbook parsing is field-for-field correct.** Every one of `amount`, `direction`,
`status_label`, `category`, `counterparty_vpa`, `rrn`, `tag`, `timestamp` matched
independent SQL on **all 44 rows, 0 mismatches**, and `settled == (statusKey==2)` held
throughout. Amounts all positive and finite.

- **Chat dedup is exact** — 0 duplicate `source_txn_id`, 0 leaks past the passbook dedup,
  every chat txn has a uniqueKey and a `unix_ms` UTC timestamp.
- **Chats** — 84/84 records, all with UTC timestamps, channel URLs and resolved
  counterparties; all 84 encrypted `rawMessage` blobs flagged; outgoing/incoming split
  56/28 and the subject's sendbird id resolves correctly for bubble alignment; the
  `_status` classifier produced **no** false "failed" from content substrings.
- **Contacts/identity** — 80/80 non-subject users, 5/5 VPA-cache rows, exactly one subject
  whose name and phone match the source.
- **Entities** — zero identifier collisions across all four key types; one subject entity;
  subject not inflated (`txn_count=0`); paid-total reconciles to the cent.
- **Jobs, diagnostics, cookies, capabilities, encrypted catalogue** — all 1:1 with source
  row counts, all timestamps decoded, no unknown enum labels, `errorCode 0` correctly
  normalised to `None`, no key material leaked from the encrypted catalogue.
- **Filters that work** — text (incl. case-insensitivity), origin, amount boundaries
  (inclusive at both ends), direction, `source_contains`, and **AND-composition** across
  two and three fields.
- **All 21 domains render** — `columns()` non-empty and `cell()` renders every column for
  every domain without raising; every domain reachable from the sidebar; both themes
  produce balanced QSS with no missing palette keys.
- **GUI runtime** — all 20 nav entries open with grid row counts **exactly matching**
  `case.db`; detail panel shows all five provenance fields plus a valid 64-hex hash;
  apply/clear round-trips; chat, timeline and map views construct and render; the map's
  offline fallback works with WebEngine absent; **theme toggle survives** double-toggling
  and toggling from the chat, map and timeline views (the riskiest path, since it deletes
  and rebuilds pages).
- **Exports** — JSON byte-identical across runs; CSV has all six provenance columns, one
  row per record, union header for heterogeneous keys, no `raw` column; KML/GeoJSON agree
  at 212 fixes with no undated points, KML well-formed XML; HTML report self-contained (no
  external asset refs), body reproducible modulo the generation timestamp, sha256 sidecar
  correct.
- **Integrity** — manifest sorted, complete, and 3 spot-checked hashes re-verified against
  the source; audit chain verifies end to end and detects tampering.
- **Pure functions** — `timestamps` (12 boundary cases incl. 0, negative, float, numeric
  string, non-numeric, and the 1e9/1e11/1e15 thresholds), `enums`, `ifsc`, `vpa`,
  `config_explain`, `protobuf` (truncated and garbage input never raise), `errorcodes`
  (unknown codes return `None` rather than guessing) — all correct.

## Lower-severity observations

- `timestamps.decode(1_000_000_000)` exactly at 1e9 → `epoch_type="unknown"`, no decode
  (needs `> 1e9`). A genuine 2001-09-09T01:46:40Z would not decode. Harmless for Paytm
  data but it is an off-by-one at a boundary.
- Date filtering on a domain with no timestamps at all (config, pref, capability) returns
  0 rows, which is arguably correct but indistinguishable from "no matches in range".
- `CookieLocationParser` pairs the **last** `lat` cookie row with the **last** `long` row,
  potentially from different hosts, and emits a single conflated fix.
- `WorkTag` (17 rows) / `WorkName` (9 rows) are unparsed; the jobs grid shows raw
  `worker_class` strings where human-readable tags exist.
- PDF export is unavailable in this environment (neither WeasyPrint nor QtWebEngine). The
  error message is clear and actionable, so this degrades correctly — but FR-G7's PDF path
  is untested and undeliverable as installed.

---

## Capstone: the project's own test suite is 100% green on this extraction

```
QT_QPA_PLATFORM=offscreen PAYTM_EXTRACTION=<real extraction> pytest -q
  -> 138 passed, 12 skipped in 14.60s
```

(The 12 skips are the `tests/_truth.json` PII-value assertions, absent by design. The 3
GUI failures reported in session 1 were purely environmental — they pass now that
`PySide6-Essentials` is installed, so **51 passed** with no extraction, 138 with one.)

Every completeness test passes. `test_A_passbook_count` passes. `test_D_source_byte_identical_after_run`
passes. `test_E_canonical_export_stable` passes. `test_timeline_includes_all_transactions`
passes.

On the same extraction, this audit established that the tool:

- omits **14 of 58** passbook transactions — **₹<amount>, 30.8% of settled value** — and
  every one of them is more recent than the newest transaction it does report;
- parses **four domains at 0%** (GPS location history, consents, search history, bank config);
- reports **74 rows the application had deleted** as `origin=live, confidence=1.0`;
- shows **1** location fix in a report while holding **211** GPS fixes;
- silently drops the final day of every date range an examiner types.

A fully green suite alongside those facts is the clearest possible statement of F-02: the
tests validate that the code does what the code does. `_raw_count` reads through the same
WAL-blind `open_ro` as the parser, so expected and actual are drawn from the same
incomplete view; `tests/synthetic.py` never sets `journal_mode=WAL`, so no fixture can
exercise the failure; and no test compares a *rendered* artefact (report/CSV) against the
data it claims to present.

**This is the finding to fix first in spirit, whatever gets fixed first in code.** Until
ground truth comes from outside the implementation, a passing suite cannot be evidence of
correctness — and for a tool whose output is intended to support admissibility, that gap
matters more than any single defect in this register.

---
---

# Session 4 addendum — GUI visual + interaction audit (2026-07-31)

`tools/audit_gui_visual.py` — **88 checks: 75 pass, 8 fail, 2 warn, 3 info** — plus **28
screenshots** rendered at 1600×950 and 900×600 in **both themes**, which I then inspected
by eye. That last step is the point: session 3 drove the GUI programmatically and every
check passed, because row counts and absent exceptions cannot see a contradiction on
screen. Every finding below was found by *looking*, then confirmed numerically.

QtWebEngine is still absent, so the Leaflet map path remains untested; the offline
fallback was rendered and inspected.

## F-33 — the same merchant appears as several separate counterparties
**Severity: high** · CONFIRMED (executed + visual) · `paytmforensics/correlate/entities.py:100-108`

Visible on the dashboard's "Top counterparties" panel, which lists **`FoodCo` 10 txns**
and **`Foodco` 3 txns** as two different parties, and in the chat conversation list,
which shows `Bistro`, `Bistro Limited` and `Bistro Media Private Limited` as three separate
conversations.

Measured across the 82 entities:

```
'bistro'              -> 2 entities: ['Bistro', 'Bistro Limited']
'foodco'           -> 2 entities: ['FoodCo', 'Foodco']
'acmefoods' -> 2 entities: ['Acme Foods Pvt Ltd', 'Acme Foods Private Limited']
'quickeats'              -> 2 entities: ['QUICKEATS', 'Quickeats']

4 real-world parties split across 8 entities
split by LETTER CASE ALONE: FoodCo/Foodco, QUICKEATS/Quickeats
```

`_merge_people` joins on exact-match keys and `entities.build` indexes
`by_name[n] = e` with no normalisation, so `"Quickeats"` and `"QUICKEATS"` never merge. The
consequences are not cosmetic:

- **FR-13's "unified counterparty" is not achieved** for the merchants this subject uses most.
- The dashboard's **"Top counterparties" ranking is wrong** — FoodCo's real total is
  split across two rows, so it is under-ranked relative to a single-name party.
- Per-counterparty totals are split, so "how much did the subject pay X?" has no single
  answer in the UI.

**Fix:** casefold-and-strip when building the name index, and normalise common company
suffixes (`Pvt Ltd` / `Private Limited` / `Limited`). Keep the raw variants in `names` for
disclosure. This is the same root as F-05 and F-26 — entity resolution needs one proper
normalise-then-union pass.

## F-34 — "212 GPS fixes" are 4 distinct coordinates, presented as a movement path
**Severity: high** · CONFIRMED (executed + visual) · `paytmforensics/gui/mapview.py`, `paytmforensics/report/geo.py`

The map view header reads **"Location map — 212 GPS fixes"** and the offline scatter draws
**two dots**. That is not a rendering bug — it is the data:

```
total fixes fed to the map / KML / GeoJSON : 212
DISTINCT coordinates                       : 4
    <lat A>, <lon A>   x128
    <lat A'>, <lon A'>  x82     (~10 m from A)
    <lat B>, <lon B>   x1      (~250 km away)
    <lat A''>, <lon A''> x1     (~10 m from A)
```

Three of the four are within roughly ten metres of each other. So the subject's location
evidence is **two places**, not 212 fixes — the diagnostic lat/lon is a coarse cached
location stamped onto every telemetry event, not a movement record.

Yet `mapview` labels them "GPS fixes", the right-hand list presents 212 chronological
entries, and both `geo.to_kml` and `geo.to_geojson` emit a **`movement_path` LineString**
through them. A LineString across 128 identical points and one outlier 250 km away is not
a journey, and in a court exhibit it invites exactly the wrong inference.

**Fix:** de-duplicate coincident fixes (the PRD's FR-5 explicitly asks to "cross-validate
and de-duplicate coincident fixes"), report distinct-position and dwell counts, and
suppress `movement_path` when the distinct-position count is trivial.

## F-35 — chat bubbles contradict themselves: header "Paid", body "Sent you ₹X"
**Severity: high** · CONFIRMED (executed + visual) · `paytmforensics/gui/chatview.py:163-165`

Straight from the rendered chat view: a right-aligned blue bubble headed
**"₹<amount> · Paid"** whose body text reads **"Sent you ₹<amount>"**.

```
messages where the header says 'Paid' but the body says 'Sent you …' : 52 of 84
messages headed 'Paid' whose own status glyph says failed/declined   : 4
```

Two separate problems in one bubble:

1. `verb = "Paid" if outgoing …` is derived from `sender_id == subject_sb`, while the body
   is Paytm's stored `messageContent`, which is written from the **recipient's**
   perspective. Both are individually defensible; shown together they directly contradict.
   An examiner reading "Sent you ₹<amount>" concludes the subject *received* ₹<amount> when the subject
   paid it — a **direction-of-funds error**, the single most consequential mistake this
   tool could induce. 52 of 84 messages are affected.
2. The verb ignores status, so a failed payment is still headed "Paid" — with a small
   "✗ failed" in the meta line underneath contradicting the bold header above it.

**Fix:** derive the header from `direction` + `settled` on the correlated transaction
rather than from sender alone, render the stored text as a quoted app string
(`app message: "Sent you ₹<amount>"`) so it is not read as the tool's own conclusion, and fold
status into the verb ("Payment failed", not "Paid").

## F-36 — the Timeline grid gives 77% of its width to a repeated word
**Severity: medium** · CONFIRMED (executed + visual) · `paytmforensics/gui/timelineview.py:97-102`

Measured column widths in the timeline table:

```
utc_iso        100px   (truncated to "27 May …")
event_type     100px
summary        100px   (truncated to "debit 82.0 …")
ref_domain     996px   <-- a short repeated word
total 1296px, viewport 1296px
```

`TimelineView.__init__` sets `setStretchLastSection(True)` but **never calls
`resizeColumnsToContents()`** (the main table page does, in `_on_nav`). So every column
sits at the 100px default and the last one absorbs all 996px of slack.

The result: in the master chronological timeline — the view an investigator would spend
most time in — **the timestamp is truncated so the time of day is invisible**, the summary
is truncated, and the widest column repeats "transaction" 299 times. `event_type` and
`ref_domain` are also near-duplicates of each other here.

**Fix:** call `resizeColumnsToContents()` (one line, matching `_on_nav`), and stretch
`summary` rather than the last column.

## F-37 — no grid can be sorted
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/gui/app.py:134`, `paytmforensics/gui/models.py`

```
QTableView.isSortingEnabled()          -> False   (setSortingEnabled never called)
RecordTableModel overrides sort()      -> No
```

Clicking any column header does nothing, in every grid. An examiner cannot sort
transactions by amount, contacts by name, or diagnostics by time. Combined with F-03
(the timeline is not stored chronologically) there is **no way to obtain a
chronologically ordered transaction list in the UI** other than reading the default
insertion order and trusting it.

Not a listed PRD requirement, but it is basic table behaviour that every examiner will try.

## F-38 — colour contrast fails WCAG AA in both themes
**Severity: medium** · CONFIRMED (computed) · `paytmforensics/gui/theme.py`

Computed WCAG 2.1 contrast ratios:

| element | dark | light | needs |
|---|---:|---:|---:|
| dim text on card (`text_dim`) | **3.48:1** | **3.08:1** | 4.5:1 |
| white on accent — selected table row | **3.75:1** | **3.75:1** | 4.5:1 |
| white on accent — primary button ("Report", "Apply") | **3.75:1** | **3.75:1** | 4.5:1 |

Everything else passes, most of it comfortably (body text is 13–16:1). But
`text_dim` carries the dashboard's case/examiner/evidence line and the map caption, and
white-on-accent is **the selected row** — the row whose provenance the examiner is reading
in the detail panel is the hardest text in the app to read. Visible in the light-theme
screenshot: the `OVERVIEW` / `FINANCIAL` sidebar group headers are very faint on white.

**Fix:** darken `accent` for text backgrounds (or use `#1f6feb`), and lift `text_dim` to at
least `#8b949e` on dark / `#6e7681` on light.

## F-39 — the map caption describes the wrong renderer
**Severity: low** · CONFIRMED (visual) · `paytmforensics/gui/mapview.py:151-154`

The caption *"Interactive map (dark basemap tiles fetched online for display; evidence
parsing is fully offline). Dashed line = movement path."* is added unconditionally. With
QtWebEngine absent the view is the static offline scatter — no tiles, no interaction, no
dashed line. The caption asserts a provenance for the display that does not match what is
on screen, which in a forensic tool is the kind of small inaccuracy worth fixing.

**Fix:** branch the caption on `self._is_web`.

## F-40 — nav group headers ignore the theme
**Severity: low** · CONFIRMED (executed) · `paytmforensics/gui/app.py:187`

`header.setForeground(Qt.gray)` hardcodes `#a0a0a4` for the `OVERVIEW` / `FINANCIAL` /
`COMMUNICATIONS` group labels, so they do not follow the palette on theme switch. There is
even an unused `QLabel#navGroup` rule in the QSS that was presumably intended for this.
Worst on light theme, where grey-on-white compounds F-38.

## F-41 — domains parsed to zero are hidden, not shown as zero
**Severity: low (but evidentiary)** · CONFIRMED (visual) · `paytmforensics/gui/app.py:190-193`

`_populate_nav` skips any domain absent from `counts`, so on this case **Consents,
Searches and Carved/deleted simply do not appear in the sidebar**, and the dashboard shows
no tile for them. This mirrors the report omitting those sections entirely.

An examiner cannot distinguish "parsed, and there were none" from "never attempted" — and
on this extraction the true answer is neither: there *are* 4 consents and 1 search, lost to
F-01. A visible "Consents (0)" row would have made the WAL problem obvious immediately.

**Fix:** show known domains with a zero count rather than hiding them.

## F-42 — the Export menu bar floats above the sidebar
**Severity: low (cosmetic)** · CONFIRMED (visual) · `paytmforensics/gui/app.py:109`

`_build_menu()` is called from inside `_topbar()` and attaches to `self.menuBar()`, so the
"Export" menu renders as a thin strip across the very top of the window, *above* both the
sidebar and the top bar. In every screenshot it appears as an orphaned label over the
sidebar's brand area rather than as part of either panel.

## Smaller visual notes

- Dashboard money figures render as bare numbers (`4004`, `4137.83`) while chat bubbles use
  `₹` — inconsistent, and an amount without a currency in a financial exhibit is sloppy.
- "Top counterparties" truncates names at 28 chars (`name[:28]`), so
  `Acme Foods Pvt Limi` is shown mid-word.
- The dashboard's Received/Paid pair carries **no caveat** that ₹<amount> across 11
  transactions is in neither figure (F-18 and unsettled rows) — see the dashboard check in
  `tools/audit_parsers.py`.
- `RecordTableModel` loads every row into memory (`_all`); 7,027 config rows opened in
  0.46s and filtered in 0.16s, so it is fine at this scale, but NFR-2's "responsive GUI on
  100k+ row grids" is unverified and the data layer is not virtualised.

## Verified good, by eye as well as by assertion

- **Both themes render correctly across all 12 views** — no clipping, no overlap, no
  unstyled widgets, no missing backgrounds. The layout also **holds at 900×600**: sidebar
  keeps its width and scrolls, the filter card reflows, the table/detail splitter survives.
- **The main table page sizes its columns properly** (`resizeColumnsToContents`), giving
  `narration` 307px and `counterparty_name` 252px — the timeline defect is local to
  `TimelineView`.
- **The detail panel is genuinely good**: source file, table, rowid, offset, origin,
  confidence and the full 64-char SHA-256, above a pretty-printed record. Provenance
  (FR-G4) is the best-executed part of the UI.
- **Cell tooltips** carry `source :: table [origin] conf=` on every cell.
- **Chat view structure is right** — per-day separators in correct order, RRN and status in
  the meta line, correct outgoing/incoming split (56/28), and the merchant threads read
  cleanly apart from F-35's wording.
- **Timeline ribbon** correctly spans Apr 2023 → Jul 2026 with per-domain colours and
  view-level chronological sorting.
- **No blank or all-grey renders** anywhere; keyboard navigation works; row selection and
  the search results page both render.

---
---

# Session 5 addendum — artifact coverage audit (2026-07-31)

`tools/audit_coverage.py` instruments `builtins.open` and `sqlite3.connect` for a full
pipeline run and records every path touched, then classifies the whole extraction.

**This closed a real gap in my own audit.** Sessions 2–4 reconciled *SQLite tables* and
*GUI surfaces*. Neither asked the simpler question: **which of the 698 files does the tool
ever open?** The answer changes the headline.

## F-43 — 47% of the extraction is read by nothing
**Severity: high** · CONFIRMED (executed)

```
files in extraction        : 698
hashed into the manifest   : 698   (integrity only — hashing is not parsing)
opened by a parser         : 370
opened by the carver       :   4
READ BY NOTHING            : 327   (46.8%)
```

Against PRD §5.1's own in-scope list (goal G1 is *"Parse 100% of the plaintext artifacts"*):

| in-scope group | files read | |
|---|---|---|
| `databases/*` (SQLite) | 15/20 (75%) | |
| `no_backup/` workdb | 1/2 (50%) | |
| `shared_prefs/*.xml` | **7/38 (18%)** | |
| `shared_jsons/*.json` | **0/28 (0%)** | |
| `files/datastore/*` | **0/2 (0%)** | |
| `files/in_app_notification_model` | **0/1 (0%)** | |
| `files/PersistedInstallation*.json` | **0/2 (0%)** | |
| `files/AppEventsLogger.persistedevents` | **0/1 (0%)** | |
| `app_webview` Local Storage | 5/10 (50%) | |
| `app_webview` Session Storage | 1/5 (20%) | |
| `app_webview` IndexedDB | **0/11 (0%)** | |
| `app_webview` Cookies / Web Data | 1/2 (50%) | `Web Data` never opened |

Six in-scope groups are at **zero**. The manifest hashes all 698 files, so the case
*documents* the existence of evidence the tool never looked at — the same shape of problem
as F-01's hashed-but-unread `-wal` files.

Genuinely-not-evidence exclusions are small: `oat/*` (4 compiled dex files), `app_mt/*`
(17 Lottie animations, sounds), and `BrowserMetrics-spare.pma`. They do not account for
327 files.

## F-44 — PRD functional requirements with no parser at all
**Severity: high** · CONFIRMED (executed)

Checked by walking `parsers.REGISTRY` and collecting every `needs` entry:

| requirement | status |
|---|---|
| **FR-3** — parse `contacts`, `contacts_phones`, `enrichment_data` | **NO PARSER REGISTERED** |
| **FR-9** — parse `discoveryDb.TBL_REMINDERS` (recurring payments) | **NO PARSER REGISTERED** |
| **FR-8** — parse `files/in_app_notification_model` | **NO PARSER REGISTERED** |
| FR-8 — FCM token from `com.google.android.gms.appid.xml` | parser registered |
| — `RealtimeSmsUploadDb` (SMS upload evidence) | **NO PARSER** |
| — `pai_signal` (71 rows) / `pai_push_signal` | **NO PARSER** |
| — `AppLocale.db` | no parser (13,006 UI-locale strings — correctly out of scope) |
| — `files/AppEventsLogger.persistedevents` | **NO PARSER** |
| — `files/datastore/*.preferences_pb` | **NO PARSER** |

Note `contacts` **is** in `carve_runner.CARVE_TARGETS`, so the tool carves a database it
never parses — deleted rows could be recovered from a table whose live rows are ignored.

The `contacts` database is 4 KB with a 49 KB WAL, and `discoveryDb.db` is 4 KB with a
45 KB WAL, so on this extraction both are empty in the immutable view anyway — but that is
F-01 masking F-44. Two independent defects, each of which alone would lose the data.

`files/in_app_notification_model` is a **Java-serialized `net.one97.paytm.local.notification`
object** carrying a `Map` and a `NetworkResponse` — real notification content, 5,347 bytes,
never opened. FR-8 names the file explicitly.

## F-45 — FR-12 catalogues 2 of 21 encrypted caches
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/parsers/encrypted.py:41`

FR-12: *"Enumerate `Data*.xml`, **encrypted `shared_jsons`**, encrypted message BLOBs;
record presence, size, owning module, and the reason they are not decryptable."*

Classified all 28 `shared_jsons`:

```
base64 ciphertext : 21
plaintext JSON    :  0
other / empty     :  7
catalogued        :  2   (TPAP_UPI.json, SMS_smssdk_pref.json — hardcoded by name)
```

**19 encrypted caches are neither parsed nor catalogued** — including `HOME.json` (80 KB),
`HOME_launch_pref.json` (54 KB), `NOTIFICATION_SDK.json` (12 KB) and `CHAT_feed.json`.
They are invisible in the case, the report and the GUI. An examiner reading the Encrypted
Artifacts section sees 5 entries and would reasonably conclude that is the full set of
undecryptable material; the true count is 24.

The three `Data*.xml` **are** correctly catalogued (they appear "unparsed" in the
shared_prefs listing only because `prefs.INTEREST` deliberately excludes them — that part
is right).

**Fix:** detect encrypted `shared_jsons` by content shape rather than a two-name allowlist.

## F-46 — `shared_prefs` is a 7-file allowlist against 38 present
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/parsers/prefs.py:18`

`INTEREST` hardcodes 7 filenames; the extraction has 38. **31 XML files are never opened**,
and the selection is a static allowlist, so any new or renamed prefs file on a future
Paytm version is silently dropped — the schema-drift tolerance claimed in PRD §13 does not
extend here.

Among the unread files are credential- and identity-bearing ones:

| file | contains |
|---|---|
| `com.sendbird.sdk.messaging.keystore.xml` | `PREFERENCE_KEY_SESSION_KEY` — a Sendbird session key, plaintext |
| `files/AppEventsLogger.persistedevents` | a Facebook `accessTokenString` + appId, Java-serialized |
| 8 × `com.sendbird.sdk.messaging.*.xml` | chat SDK lifecycle/state |
| 6 × `com.facebook.*` | SDK settings, attribution, gatekeepers (48 KB) |

Nothing leaks (they are never read, so never emitted), but they are **unexamined
evidence** — and the two credentials above are exactly the kind of artifact an examiner
would want catalogued, whether or not the value is redacted.

## F-47 — the carver sees 4 of 20 databases and none of the 18 WALs
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/carving/carve_runner.py:19`

```python
CARVE_TARGETS = ("chatDb.db", "passbook.db", "cache_database", "contacts")
```

**16 of 20 databases are never carved** — including `ups_database` (consents),
`search_db`, `bank_signal` (location), `PaytmMessageDatabase` (notifications),
`discoveryDb.db` (reminders) and `storefront_db_try3`. Deleted rows in any of those are
unrecoverable by construction, not by source state.

And `SqliteCarver.__init__` reads only the main database file, so **none of the 18 `-wal`
files is ever carved** — even though a WAL is a ring of *old page images* and is the single
richest source of recently-deleted rows on a live-extracted device. This is very likely why
carving returned 0 records (see the session-2 note): the deletions are in the WALs.

METHODOLOGY §8 honestly says recovery depends on source state — but "we only look at 4 of
20 files and never at the WAL" is a **scope** limit, not a source-state limit, and is not
disclosed anywhere.

## F-48 — the `Account` model is dead code
**Severity: low** · CONFIRMED (executed) · `paytmforensics/core/models.py:98`

`grep -rn "Account("` across `parsers/`, `correlate/` and `carving/` returns **nothing**.
The `account` domain is never emitted, yet FR-1 requires the subject's *"linked bank
account (IFSC/branch prefix), bank type"*. Some of that is recovered onto `Transaction`
(`account_used`, `account_bank`, `account_branch`) but there is no account-level record,
which is also why the dashboard's subject card shows **Bank: —**.

**Precision note:** `Consent` and `SearchQuery` initially looked dead too. They are not —
`ConsentParser` and `RecentSearchParser` both exist and construct them; they emitted zero
records on this extraction purely because of F-01. Only `Account` is genuinely unreachable.
(My first version of this check instantiated each dataclass, which raised on the required
`provenance` argument and silently reported *nothing* — fixed to read the field default.)

## Revised headline

Combining F-01 and F-43, on this extraction the tool:

- **opens 53%** of the files in the evidence;
- of the SQLite rows it does reach, **misses a further 306** to the WAL, including four
  domains at 0%;
- and **hashes 100%** of it into a manifest that certifies the integrity of data the case
  never examined.

PRD goal **G1 ("parse 100% of the plaintext artifacts") is not met**, and success metric
§12 ("100% of plaintext artifacts in the reference extraction parsed without error") is not
met either — though note the *"without error"* half is true: nothing crashed. Zero parse
errors and 47% coverage are not in tension; they measure different things, and only one of
them is currently measured by the test suite.

---
---

# Session 6 — ALL FINDINGS FIXED AND VERIFIED (2026-07-31)

Branch `fix/audit-findings`. Every one of the 48 findings is addressed. Fixed **by root
cause**, not one-by-one, then re-verified with all five harnesses plus the project suite.

## Verification results (real extraction, after fixes)

| harness | before | after |
|---|---|---|
| `tools/audit_parsers.py` | 82 pass / 11 fail / 5 warn | **101 pass / 0 fail / 0 warn** |
| `tools/audit_features.py` | 196 pass / 7 fail / 10 warn | **227 pass / 0 fail / 1 warn** |
| `tools/audit_gui_visual.py` | 75 pass / 8 fail / 2 warn | **90 pass / 0 fail / 1 warn** |
| `tools/audit_coverage.py` | 12 PRD groups, 6 at 0% | **all 12 PRD groups at 100%** |
| project suite (`pytest`, real extraction) | 138 pass (but blind — F-02) | **164 pass, 0 fail** |
| new `tests/test_audit_regressions.py` | — | **26 tests, all written to fail pre-fix** |

The two residual warnings are environmental/architectural, not defects: **PDF export**
(neither WeasyPrint nor QtWebEngine installed here) and **grid virtualisation**
(`RecordTableModel` holds all rows in memory; fine at 7.9k, unproven at NFR-2's 100k).

## What changed in the evidence picture

| | before | after |
|---|---:|---:|
| transactions | 86 | **100** |
| ledger ends | <date> | **<date>** |
| settled paid (recovered value) | baseline | **+78%** |
| consents | 0 | **4** |
| searches | 0 | **1** |
| location fixes | 1 | **21** |
| config items | 7,027 | **7,921** |
| encrypted artifacts catalogued | 5 | **30** |
| preferences | 129 | **229** |
| timeline events / out-of-order pairs | 299 / **48** | 425 / **0** |
| new domains | — | `account` (2), `channel` (41) |
| report sections previously absent | — | Contacts, Bank accounts, Conversations, Consents, Searches, WAL exposure |

## Fixes by root cause

**Root 1 — nothing ever opened a `-wal` (F-01, F-16, F-47, and it masked F-44).**
`ingest/sqlite_ro.py` gained `open_with_wal()` (copy db+wal+shm to scratch, verify the
source SHA-256 before and after, open the *copy* `mode=ro`) and `open_best()`, which picks
it when a WAL exists. Parsers call `self.open(art)`; `provenance.read_mode` records
`immutable` vs `wal_applied`. `Case.wal_report()` writes `wal_report.json`, the CLI prints
a banner and the HTML report gains a **WAL exposure** section. The carver now covers every
SQLite database (magic-byte detection, not a 4-name list) *and* every `-wal` via a new
`WalCarver` that applies the same unallocated-region logic per frame page.
**EV-1 still holds** — `test_F01_source_is_byte_identical_after_a_wal_run` proves the
source is unchanged (sha + size + mtime) after a full run including carving.

**Root 2 — entity resolution (F-05, F-26, F-33).** Replaced first-hit-wins with a real
union-find iterated to a fixed point (order-independent), added `norm_name()` (casefold +
company-suffix stripping) so `FoodCo`/`Foodco` and
`Acme Foods Pvt Ltd`/`Acme Foods Private Limited` merge, seeded
entities from **transaction counterparties** as well as `person`, and added phone as a
join key.

**Root 3 — filters (F-08, F-15, F-20, F-21, F-22, F-23, F-24, F-29).** Dates are now
parsed to instants (`parse_bound`), a bare `date_to` means end-of-day, unpadded and
regional formats parse, `created`/`start_time` count as event times, criteria that a
domain cannot satisfy are skipped instead of emptying the grid, `field_equals` no longer
matches absent fields against `"None"` and handles bools, free-text searches values only
(matching global search), invalid input is surfaced in an **inline banner** — and FR-G3
filter presets are implemented, persisted per case in `filter_presets.json`.

**Presentation (F-03, F-04, F-06, F-07, F-19, F-31, F-36, F-37, F-38, F-39, F-40, F-41,
F-42).** Timeline sorted at build time with a stable tiebreak and gained
job/crash/appstate/cookie/webcache/channel builders; `DISPLAY_COLUMNS` widened (incl.
diagnostic `latitude`/`longitude`); the report gained the `person` and `account` sections,
per-record rowid/offset/hash/read-mode/confidence, and a default 500-row cap; CSV gained
flat `timestamp.utc_iso` columns; the Timeline grid sizes its columns and stretches
`summary`; **every grid is sortable**; `accent_on` fixes white-on-accent contrast and
`text_dim` was lifted (both themes now pass WCAG AA); the map caption describes the
renderer actually in use; nav headers follow the palette; zero-count domains are shown as
`(0)` rather than hidden; the menu bar is built explicitly.

**Interpretation (F-17, F-18, F-28, F-34, F-35).** `txnCategory=2` relabelled `money_in`
(the app's own `txnTag` said "Money Received" on 3 of 4 rows) and `tag` is now displayed;
`COMPLETED` chat payments carrying an RRN are settled and directed (₹<amount> no longer sits
in neither total); `PushData.expiry` moved out of `timestamp` into its own field with a
structural `is_dedup_record` flag; the map reports **distinct positions** and both KML and
GeoJSON suppress `movement_path` when positions are trivial; chat bubbles derive the verb
from direction+status and quote the app's stored text as `app text: "…"` so "Sent you ₹<amount>"
is never read as the tool's own conclusion.

**Coverage (F-43, F-44, F-45, F-46, F-48, F-32).** New `parsers/misc_stores.py`
(contacts db FR-3, reminders FR-9, in-app notifications FR-8, SMS upload, pai_signal,
subject bank `Account` FR-1) and `parsers/appfiles.py` (DataStore protobuf, Firebase
installation, remote config, Facebook events, IndexedDB, Web Data). `shared_prefs` parses
all 38 files with pattern-based redaction instead of a 7-name allowlist; encrypted
`shared_jsons` are detected by content shape (2 → 30 catalogued); `TBL_CHANNELS` becomes
its own `channel` domain so the 30 message-less conversations are visible.

**Integrity & CLI (F-25, F-27).** `AuditLog.verify()` ships, with
`--verify-audit`; the CLI refuses a nonexistent extraction path.

**Docs (F-09, F-11, F-13).** README's `message` description corrected, the stale
`transactions.py` comment replaced, METHODOLOGY §1/§2 rewritten for the WAL copy path and
what the report actually surfaces, and the README/PRD "carving roughly doubles / ≈2×"
claims softened to state that yield depends on source state and may be zero.

**F-02 — the verification gap.** `_raw_count` in `test_rigorous.py` / `test_m2.py` now
reads WAL-aware, `synthetic.py` supports `journal_mode=WAL`, and the decisive WAL tests use
**literal expected values** against a purpose-built fixture rather than routing ground
truth through the implementation's own reader.

## Things deliberately NOT "fixed"

- **Carving still recovers 0** on this extraction, now across all 20 databases and all 18
  WALs. That is the honest result, and it is why the README/PRD claims were softened rather
  than the carver tuned to produce hits.
- **`AppLocale.db`** (13,006 UI translation strings) remains unparsed by design.
- **PDF export** needs WeasyPrint or QtWebEngine; neither is installed here. The failure
  message is clear and actionable.
- **Grid virtualisation** — flagged, not rebuilt; NFR-2's 100k-row claim remains unproven.

## Defects found in the FIX work itself

Recorded because they are the reason the "verified" claim is worth anything:

1. A **modal** `QMessageBox` in the new invalid-input warning **hung every headless run**.
   Caught by the harness stalling; replaced with an inline banner.
2. Channel records first landed in the `message` domain, inflating Chats 84 → 125. Caught
   by the parser audit; moved to a dedicated `channel` domain.
3. Reading Data*.xml headers emitted the key name `datak` into the catalogue — caught by
   the project's **own** `test_J_no_plaintext_secret_in_encrypted_records`. Now records
   entry *count* and shape only.
4. The new `account` domain was not reachable from the sidebar. Caught by the visual audit.
5. Several harness checks went stale after the fixes (measuring the pre-fix world) and
   would have produced false PASSes or false FAILs; each was corrected to assert the
   outcome rather than the old implementation detail. The coverage harness in particular
   was **inflating coverage to 90%** because the carver's magic-byte sniff opens every
   file — corrected to count parser reads only, which is why the honest figure is 27.3%
   unread (all UI animations, sounds and cache bookkeeping) rather than 9.7%.


---
---

# Session 7 — hardening pass (2026-07-31)

Deliberately did **not** re-run the green harnesses. Instead attacked paths never audited:
export routes that bypass the display layer, packaging, entry-point robustness, and
degenerate input. Four more defects, all fixed.

## F-49 — the geo export bypassed the masking control
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/report/geo.py`

`_fixes_from_casedb` reads `case.db` with its own `sqlite3.connect`, not through
`DataSource`. So "Locations → KML/GeoJSON" exported **exact coordinates even with
Hide-sensitive-data switched on** — the single export path that could silently defeat the
control an examiner had deliberately enabled.

Fixed: `geo.export(..., mask_sensitive=)`, wired to the GUI toggle (with a confirmation
noting coordinates are blurred) and to a new CLI `--geo`, which follows `--redact-report`.
Verified: masked GeoJSON rounds to 1 decimal vs the exact source pair; masked KML contains
no exact coordinate.

## F-50 — the GUI crashed on a wrong folder
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/gui/datasource.py`

Pointing the GUI at a directory with no `case.db`, or a corrupt one, produced a raw
`OperationalError: no such table: records` / `DatabaseError: file is not a database`
traceback. Choosing the wrong folder is an ordinary mistake, and NFR-3 requires that a
corrupt artifact must not crash the run.

Fixed: a `CaseOpenError` carrying an actionable message (including the exact CLI command to
build a case), caught in `launch()` and shown as a dialog; `domains()`/`load()` degrade to
empty rather than raising; `run_gui.py` validates its argument up front.

## F-51 — the packaging spec only collected the parsers
**Severity: medium** · CONFIRMED (read) · `paytmforensics.spec`

`hiddenimports` was `collect_submodules("paytmforensics.parsers")` alone, but `Case` and
`MainWindow` import the **carver, correlate, report, enrich, ingest and GUI view** modules
*lazily inside methods*. A packaged build that dropped any of them would fail only in the
field, at the moment an examiner pressed Carve or Report.

Fixed: every subpackage collected explicitly, plus the optional WebEngine/PrintSupport
modules the map and Qt PDF path need. A regression test asserts the spec names them.

## F-52 — `raw` masking was not recursive (found by the new harness section)
**Severity: medium** · CONFIRMED (executed) · `paytmforensics/core/privacy.py`

`mask_record` masked only top-level strings inside `raw`, so identifiers one level down
survived — a customer ID inside `raw["cart"]` and a phone number inside a nested source
column. Fixed with a fully recursive `mask_any`. This was caught by the masking section
added to `tools/audit_features.py`, i.e. by a check written *after* the feature looked done.

## Verified clean in this pass

- **EV-1 on real evidence**: 698 files byte-identical (sha + size + mtime) after a full run
  including WAL copying and carving; `--verify` reports ok.
- **EV-6**: two independent runs produce an identical canonical export hash.
- **No scratch leaks**: zero `ptmf_wal_*` directories remain in `/tmp`.
- **All 64 modules import cleanly.**
- **Every CLI flag end-to-end**: `--verify --report --geo --redact-report --verify-audit`;
  the audit chain verifier reports `ok: true, entries: 48, breaks: []`.
- **NFR-7**: a Devanagari + emoji extraction *path* and content round-trip
  (`चाय ☕ ₹9`, `🥘 Food`).
- **Idempotency**: three runs into one case dir still yield one transaction.
- **A 50,000-character field** renders in a grid cell, masked and unmasked.
- **Masking performance**: 16k masked cells in 0.13s (27x the unmasked path, but
  ~0.5s for the largest 7.9k-row domain — not user-visible).
- **Masking coverage**: zero leaks across all 23 grids, the detail panel, the redacted
  report, and now the geo exports.

## Still open, unchanged and stated

- **PDF export** requires WeasyPrint or QtWebEngine; neither is installed in this
  environment. The error is clear and actionable.
- **Grid virtualisation** — `RecordTableModel` holds every row in memory. Fine at 7.9k
  (0.46s open, 0.16s filter); NFR-2's "100k+ rows" remains unproven.
- **Carving recovers 0** on this extraction across all 20 databases and all 18 WALs.
- `AppLocale.db` (13k UI translation strings) is out of scope by design.
