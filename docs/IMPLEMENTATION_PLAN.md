# Implementation Plan
## PaytmForensics — Court-Admissible Offline Parser (Python Desktop GUI)

Companion to `PRD.md`. This document defines architecture, stack, module breakdown, data model,
project layout, phased milestones, validation, and packaging.

---

## 1. Architecture overview

A layered pipeline. Each layer is independently testable; data flows one way.

```
┌──────────────────────────────────────────────────────────────────────┐
│ 0. CASE / INTEGRITY LAYER                                              │
│    - Folder intake, working-copy, SHA-256/MD5 manifest, audit log,    │
│      chain-of-custody metadata, read-only enforcement                 │
└───────────────┬──────────────────────────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 1. INGESTION LAYER                                                    │
│    - Locate & classify artifacts (DB / XML / JSON / leveldb / proto)  │
│    - Open SQLite read-only (mode=ro&immutable=1)                      │
└───────────────┬──────────────────────────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 2. PARSING LAYER (one parser module per artifact domain)              │
│    - Live-row extraction → normalized records                         │
└───────────────┬──────────────────────────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 3. CARVING LAYER                                                      │
│    - SQLite freelist + slack/unallocated page recovery                │
│    - Record validation + dedupe vs live + confidence scoring          │
└───────────────┬──────────────────────────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 4. ENRICHMENT LAYER                                                   │
│    - Enum decode (txnIndicator/statusKey/category/errorCode)          │
│    - Timestamp decode (Unix ms / WebKit µs), VPA PSP parse,           │
│      config-vs-default diff, cached-getInfo parse                     │
└───────────────┬──────────────────────────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 5. CORRELATION LAYER                                                  │
│    - Entity resolution on VPA / customerID / phone / deviceID /       │
│      channelURL → unified People, Accounts, Transactions, Events      │
│    - Master timeline builder                                          │
└───────────────┬──────────────────────────────────────────────────────┘
                ▼
┌───────────────────────────────┬──────────────────────────────────────┐
│ 6. PRESENTATION (PySide6 GUI)  │ 7. REPORTING / EXPORT                │
│    - Grids, filters, detail,   │    - Jinja2 HTML → PDF (WeasyPrint)  │
│      map, relationship, timeline│   - JSON / CSV, hashed manifest     │
└────────────────────────────────┴─────────────────────────────────────┘
```

**Intermediate store:** parsed/correlated data is written to a local **case SQLite database**
(`case.db`, separate from evidence) that backs the GUI grids and exports. This keeps the GUI fast
and makes exports reproducible.

---

## 2. Technology stack

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 | Consistent with existing analysis scripts; rich ecosystem |
| GUI | **PySide6 (Qt6, LGPL)** | Professional desktop UX, virtualized tables, mature; used by real forensic tools |
| Table/filter model | Qt `QAbstractTableModel` + `QSortFilterProxyModel` (custom) | Handles 100k+ rows, multi-column filtering |
| SQLite access | stdlib `sqlite3` with `mode=ro&immutable=1` URIs | Read-only guarantee |
| Carving | Custom module (pure Python) | We already prototyped freelist/slack scanning |
| XML prefs | stdlib `xml.etree` | Android prefs are simple XML |
| LevelDB (WebView) | `plyvel` if available, else best-effort raw `.ldb`/`.log` scan | LevelDB is awkward; degrade gracefully |
| Protobuf | `protobuf` lib + bundled `.proto` (compiled) | Decode analytics/transport payloads |
| Templating | Jinja2 | HTML report generation |
| HTML→PDF | WeasyPrint (primary) / Playwright-chromium (fallback) | Offline PDF rendering |
| Hashing | stdlib `hashlib` | SHA-256 + MD5 manifest |
| Maps | Leaflet + bundled offline tiles (optional) / static coordinate export | No network |
| Packaging | PyInstaller | Single installable for examiner workstations |
| Testing | pytest + reference dataset | Forensic regression validation |

---

## 3. Module breakdown

```
paytmforensics/
├── core/
│   ├── case.py            # case lifecycle, metadata, working-copy mgmt
│   ├── integrity.py       # hashing, manifest, read-only enforcement, verify
│   ├── audit.py           # append-only audit log
│   ├── artifact.py        # artifact discovery & classification
│   ├── models.py          # normalized dataclasses (Record, Person, Txn, Event...)
│   └── casedb.py          # writes parsed data into case.db
├── ingest/
│   └── sqlite_ro.py       # safe read-only opener + schema introspection
├── parsers/               # FR-1..FR-10, one module per domain
│   ├── identity.py
│   ├── transactions.py    # passbook + chat payment data (incl. RRN)
│   ├── contacts.py        # TBL_USERS, contacts, cache_database
│   ├── chats.py           # channels + messages + crossref
│   ├── location.py        # bank_signal + webview cookies
│   ├── consents.py        # ups_database
│   ├── jobs.py            # androidx.work.workdb (+ WAL)
│   ├── notifications.py   # PaytmMessageDatabase, in_app_notification_model
│   ├── search_state.py    # search_db, storefront, reminders
│   ├── config_diag.py     # appManager*, error analytics
│   ├── prefs.py           # shared_prefs/*.xml
│   └── encrypted.py       # FR-12 catalogue of TEE-bound artifacts
├── carving/
│   ├── sqlite_pages.py    # page parsing, freelist walk, slack scan
│   ├── record_carver.py   # cell/record reconstruction + validation
│   └── patterns.py        # RRN/VPA/phone/amount validators
├── enrich/
│   ├── enums.py           # decode tables (txnIndicator/statusKey/category)
│   ├── errorcodes.py      # error_mapper.json
│   ├── timestamps.py      # epoch detection + UTC/local decode
│   ├── vpa.py             # PSP handle parsing (@icici/@ptybl/...)
│   └── config_diff.py     # vs bundled bankappmanager defaults
├── correlate/
│   ├── entities.py        # entity resolution / join keys
│   └── timeline.py        # master timeline merge
├── report/
│   ├── html.py            # Jinja2 renderer
│   ├── pdf.py             # WeasyPrint/Playwright
│   ├── exporters.py       # JSON / CSV
│   └── templates/         # report templates
├── gui/
│   ├── app.py             # PySide6 entry, main window, left-nav
│   ├── widgets/           # filterable grid, detail+provenance, map, graph, timeline
│   └── filters.py         # filter model, presets
├── resources/             # bundled decode tables, .proto, offline map tiles
│   ├── error_mapper.json
│   ├── bankappmanager_defaults.json
│   └── proto/
├── tests/
│   ├── reference_extraction/   # synthetic known dataset
│   └── test_*.py
└── main.py                # launches GUI; also exposes `--cli` headless mode
```

---

## 4. Normalized data model (core entities)

- **Record** (base): `source_file, source_table, rowid_or_offset, origin(live|carved), confidence, raw{}`
- **Subject / Person**: identifiers (customerId, phone, VPA[], name, deviceId), is_subject flag,
  account-age, KYC state, linked bank.
- **Account/Instrument**: bank name, IFSC/branch prefix, account type, masked number.
- **Transaction**: amount, direction, counterparty(ref Person), VPA, RRN, mode, status(raw+decoded),
  category(raw+decoded), tag, narration, instrument(ref), timestamp(raw+utc), source, origin.
- **Message**: thread/channel, sender(ref), type(transfer/request/text), content/preview, amount,
  status, timestamp, encrypted_blob_present(bool).
- **LocationFix**: lat, long, speed, source(signal|cookie), timestamp(raw+utc), pincode?.
- **Consent**, **Job**, **Notification**, **Search**, **ConfigItem**, **DiagnosticEvent**,
  **EncryptedArtifact**.
- **TimelineEvent**: `(utc, type, summary, ref)` — the merged view.

All persisted into `case.db` with stable ordering for reproducible export.

---

## 5. Phased milestones

> Estimates assume one developer; adjust as needed. Each phase ends with a demoable build + tests.

### M0 — Foundations & integrity (core trust layer)
- Project scaffold, packaging skeleton, logging.
- `core/case`, `core/integrity` (hash manifest, working copy, read-only opener), `core/audit`.
- Artifact discovery/classification over the real extraction.
- **Exit:** open a folder, produce a verified hash manifest + audit log; nothing parsed yet.

### M1 — Core parsers + case DB
- Normalized `models`, `casedb`.
- Parsers: identity, transactions (passbook + chat RRN), contacts, chats, location, consents.
- Enrichment: timestamps, enums, error codes, VPA.
- **Exit:** `--cli` run fills `case.db`; JSON export of the six core domains with provenance.

### M2 — Remaining parsers + correlation + timeline
- Parsers: jobs (workdb + WAL), notifications, search/state, config/diag, prefs, encrypted catalogue.
- Correlation engine (entity resolution) + master timeline.
- Config-vs-default diffing.
- **Exit:** full normalized model; per-counterparty profiles; merged timeline in JSON/CSV.

### M3 — Carving (deleted-record recovery)
- `sqlite_pages` + `record_carver` + validators; dedupe vs live; confidence scoring.
- Integrate carved records into model with `origin=carved`.
- **Exit:** carving recovers ≥ manual-analysis counts on reference data; clearly labelled.

### M4 — GUI
- PySide6 shell, left-nav, virtualized filterable grids for every domain.
- Granular filter model + saved presets; detail + provenance panel.
- Map view, relationship view, timeline view.
- Audit/integrity surface.
- **Exit:** examiner can browse, filter, and inspect provenance entirely in the GUI.

### M5 — Reporting & export
- Jinja2 HTML report (full + filtered subset), PDF rendering, JSON/CSV exporters, hashed output.
- Case metadata + manifest embedded in reports.
- **Exit:** end-to-end filtered court report generated from the GUI.

### M6 — Hardening, validation & packaging
- Reference/synthetic dataset + regression tests; reproducibility check.
- Schema-drift tolerance, error resilience, performance pass on large grids.
- PyInstaller build, user/methodology documentation.
- **Exit:** signed release candidate + validation report.

### M7 (optional, best-effort) — WebView LevelDB / protobuf / IndexedDB
- LevelDB parse for Local Storage, protobuf decode for analytics/transport payloads.
- **Exit:** labelled best-effort results integrated into the model.

---

## 6. Validation & testing strategy (admissibility-critical)

- **Reference dataset:** a synthetic Paytm-shaped extraction with known ground truth committed to
  `tests/reference_extraction/`. Every parser asserts exact expected output.
- **Reproducibility test:** run twice, diff JSON exports → must be byte-identical.
- **Read-only test:** assert source files' hashes are unchanged after a full run.
- **Carving test:** seed known deleted rows, assert recovery + correct `carved` labelling + no
  false positives on a clean DB.
- **Timestamp test:** known Unix-ms and WebKit-µs values decode to expected UTC.
- **Provenance test:** every record in the export has non-null source attribution.
- **Resilience test:** truncated/corrupt DB → run completes, logs error, no crash.
- CI runs the full suite on every change.

---

## 7. Packaging & distribution
- PyInstaller one-folder build (Windows primary), bundling `resources/` (decode tables, proto,
  optional offline map tiles).
- Versioning: tool version + per-parser version surfaced in UI and reports.
- No auto-update / no telemetry (NFR-4).
- Ship: installer, user guide, **methodology document** (how parsing/carving works, limitations),
  and the validation report.

---

## 8. Key design decisions (rationale)
- **Parse-only, folder input:** keeps acquisition (and its legal authorisation) separate from
  analysis — cleaner chain of custody.
- **Separate `case.db`:** never mix derived data with evidence; enables fast GUI + reproducible export.
- **Read by column name, tolerate missing columns:** survives Paytm version drift.
- **Everything carries provenance + live/carved flag:** non-negotiable for court use.
- **Encrypted artifacts catalogued, never cracked:** legally and technically correct; TEE-bound
  keys are unrecoverable offline (see crypto analysis of `DataUPI.xml`).

---

## 9. Open items to confirm before/during M0
- Exact list of examiner workstation OS versions (Windows-only vs cross-platform priority).
- Whether offline map tiles may be bundled (size/licensing) or location stays list/coords-only.
- PDF engine preference (WeasyPrint vs Playwright) given workstation constraints.
- Whether a headless `--cli` mode is required for batch processing in v1 (recommended: yes).
```
