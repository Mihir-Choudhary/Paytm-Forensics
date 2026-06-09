# PaytmForensics

> **Status: Work in Progress (WIP).** This project is under active development. APIs,
> output schemas, and the GUI are subject to change. It has been exercised on a single
> real-world extraction and a synthetic ground-truth dataset; it has **not** yet been
> independently validated for courtroom use. Do not use the output as evidence without
> applying your own validation procedures first.

Offline, read-only forensic parser for the Paytm Android app (`net.one97.paytm`).
Point it at an already-acquired app-data directory; it produces a structured case
folder with per-record provenance, integrity hashing, and a self-contained report.

```
extraction (net.one97.paytm/)  →  PaytmForensics  →  case.db + manifest.json
                                                     + audit.log + report.html
                                                     + KML / GeoJSON / CSV / JSON
```

Acquisition is **out of scope by design** — acquire the data with your validated
method (Cellebrite UFED, MSAB XRY, ADB backup, full file-system image, etc.), then
run this tool against the extracted folder. The source files are opened read-only
and re-hashed after the run to prove they were not modified.

---

## Table of contents
- [Why](#why)
- [Features](#features)
- [Install](#install)
- [Usage](#usage)
- [Output layout](#output-layout)
- [What it parses](#what-it-parses)
- [Forensic controls](#forensic-controls)
- [Limitations](#limitations)
- [Architecture](#architecture)
- [Tests](#tests)
- [Packaging](#packaging)
- [Project status & roadmap](#project-status--roadmap)
- [Disclaimer](#disclaimer)

---

## Why

Paytm is a heavily used UPI / wallet / commerce app in India. Its on-device data
is spread across ~30 SQLite databases, encrypted XML stores, SharedPreferences,
WebView caches and analytics blobs. Manually correlating a UPI transaction to
its chat receipt, the device's location at that instant, and the background job
that synced it is error-prone.

PaytmForensics decodes those artifacts into one chronological case with stable
identifiers, so an analyst can answer questions like *"who did the subject pay
₹X to, from where, and is there a chat receipt for it?"* without writing SQL.

## Features

- **20 parsed domains** — UPI transactions, chat ledger, contacts, entities,
  locations, consents, jobs, notifications, search history, config / feature
  flags, diagnostics, encrypted-artifact catalogue, preferences, deleted-record
  carving, WebView cookies / storage / cache, capabilities, app-state / cart,
  crash sessions, and a master timeline.
- **NPCI RRN + UPI VPA extraction**, with cross-validation between the passbook
  table and chat payment messages (no double-counting).
- **Entity resolution** — merges phone / VPA / customer-id / sendbird-id across
  sources into a unified counterparty.
- **Deleted-record carving** of SQLite freelist pages, intra-page freeblocks,
  and the gap between cell-pointer array and cell-content area. Two passes:
  structured record reconstruction, then pattern recovery (VPA / RRN / phone /
  txn-id). Carved rows are deduped against live ones.
- **Offline IFSC → bank/branch resolution** and bundled error-code mapping.
- **Master timeline** that merges every timestamped record across domains.
- **GUI** (PySide6) — dashboard, per-domain tables with filters, global search,
  WhatsApp-style chat view, Leaflet map of GPS fixes (offline fallback when no
  network), visual timeline ribbon, light / dark theme.
- **Reports** — self-contained HTML with inline SVG charts and per-record
  provenance; PDF via WeasyPrint when available.
- **Exports** — deterministic JSON / CSV per domain, plus KML / GeoJSON for
  location fixes.
- **Forensic controls** — read-only SQLite opens, SHA-256 + MD5 manifest,
  hash-chained audit log, byte-identical-source verification, deterministic
  exports, length-only redaction of token-bearing preference keys.

## Install

Python 3.10+ recommended.

```bash
git clone https://github.com/<your-org>/paytmforensics
cd paytmforensics
python -m pip install -r requirements.txt
```

Core parsing/carving uses only the Python standard library. The extra
dependencies are:

| Package      | Used for                                                  |
| ------------ | --------------------------------------------------------- |
| `PySide6`    | GUI (QtWebEngine for the map view)                        |
| `Jinja2`     | HTML report templating                                    |
| `weasyprint` | optional — HTML → PDF conversion                          |
| `protobuf`   | best-effort decoding of analytics blobs                   |
| `plyvel`     | optional, Linux/macOS only — WebView Local Storage (LDB)  |
| `pytest`     | running the test suite                                    |

## Usage

### Headless (CLI)

```bash
python -m paytmforensics.cli \
    --extraction "/path/to/net.one97.paytm" \
    --out       "/path/to/case_dir" \
    --case-id   PTM-2026-001 \
    --examiner  "Det. Rao" \
    --evidence  MOB-7 \
    --verify --report
```

`--verify` re-hashes every input after parsing and asserts the source is
byte-identical. `--report` writes a self-contained `report.html` next to
`case.db`.

### GUI

```bash
python run_gui.py --extraction "/path/to/net.one97.paytm" --out "/path/to/case_dir"
# ...or open an existing case:
python run_gui.py /path/to/case_dir
```

The GUI loads from the case DB only — no further access to the original
extraction is needed once the case has been built.

## Output layout

```
case_dir/
├── case.db            # SQLite — one row per parsed record, with provenance
├── manifest.json      # SHA-256 + MD5 + size of every input file
├── audit.log          # hash-chained log of every tool action
├── case_meta.json     # case ID / examiner / evidence number / notes / timestamps
├── report.html        # (with --report) self-contained HTML report
├── report.html.sha256 # SHA-256 of the report
└── exports/           # (optional) JSON / CSV / KML / GeoJSON
```

`case.db` has a single `records` table with columns
`(id, domain, data JSON, provenance JSON)` — easy to query directly with SQL or
load into any analysis notebook.

## What it parses

| Domain         | Source(s)                                                      |
| -------------- | -------------------------------------------------------------- |
| identity       | Paytm preferences, KYC state, bank-app prefs                   |
| transaction    | Passbook DB + chat ledger (deduped on `sourceTxnId`)           |
| message        | Chat DB (Sendbird), non-payment messages                       |
| person         | Contacts DB                                                    |
| entity         | resolved counterparties (phone / VPA / sendbird / customer-id) |
| location       | location DB + diagnostic GPS fixes                             |
| consent        | consent DB                                                     |
| job            | WorkManager DB                                                 |
| notification   | push DBs (push-dedup bookkeeping excluded from timeline)       |
| search         | search history                                                 |
| config         | storefront feature-flag cache + classified config              |
| diagnostic     | HawkEye / analytics rows with a real event time                |
| encrypted      | `Data.xml` / `DataUPI.xml` / `DataERUPEE.xml` catalogue        |
| pref           | SharedPreferences (token values length-redacted)               |
| carved         | recovered freelist / freeblock / slack records                 |
| cookie         | WebView cookies (WebKit µs timestamps decoded)                 |
| webstorage     | WebView Local Storage / Session Storage / IndexedDB (BE)       |
| webcache       | Chromium Service-Worker cache index + bodies                   |
| capability     | enumerated device/app capabilities                             |
| appstate       | `common_storage` cart / reminders (Java-serialized)            |
| crash          | Crashlytics session previews                                   |
| timeline       | merged chronological view of every domain above                |

"BE" = best-effort: surfaced honestly, with confidence, never invented.

## Forensic controls

Detailed in [`METHODOLOGY.md`](METHODOLOGY.md). Summary:

- **Read-only opens** — every SQLite database is opened with the URI
  `file:<path>?mode=ro&immutable=1`. SQLite is forbidden from writing,
  journaling, or replaying the WAL against the source.
- **Manifest + verify** — SHA-256 and MD5 of every input file are recorded at
  ingest. `--verify` re-hashes after the run and asserts no change. There is a
  regression test that proves the source is byte-identical (hash + size +
  mtime) before and after a full pipeline run.
- **Hash-chained audit log** — every tool action (ingest, parse, carve,
  correlate, verify, export) is appended with a SHA-256 chain over the previous
  entry.
- **Per-record provenance** — every emitted record carries
  `source_file`, `source_table`, `rowid` (or `byte_offset` for carved data),
  `origin ∈ {live, carved}`, `confidence`, and the source file's
  `ingest_sha256`. Reports and exports surface these fields.
- **Deterministic exports** — JSON / CSV exports and the HTML report body are
  byte-stable across runs (the only varying field is the report-generation
  timestamp).
- **Secret hygiene** — token-bearing preference keys (`sso_token`,
  `pb_auth_token`, `afUninstallToken`, etc.) are redacted to length-only in
  output so live credentials aren't copied into a report.

## Limitations

- **Encrypted stores are not decrypted.** `Data.xml`, `DataUPI.xml`,
  `DataERUPEE.xml` use AES-256-GCM with a data key wrapped by an RSA-2048
  (OAEP-SHA256) keystore key generated in the device's TEE; the private key is
  non-exportable and absent from any filesystem extraction. The tool
  **catalogues** these (name, size, owning module, cipher, keystore alias,
  reason) and stops there. Offline decryption is not possible — it requires the
  original device with the app's runtime / keystore.
- **Carving recovery depends on source state.** `secure_delete=ON`, vacuum, and
  checkpointing all reduce what's left to carve. A vacuumed DB may yield zero
  recoverable deletes; the tool reports that honestly rather than guessing.
- **Best-effort decoders.** WebView LevelDB / IndexedDB and protobuf analytics
  blobs are decoded heuristically and labelled as such. Nothing is invented.
- **No acquisition.** The tool does not pull data off a device. Use a validated
  acquisition method first.
- **No network access for evidence processing.** Map tiles in the GUI are
  fetched online from a public CartoDB tile server for display only; the
  fallback offline scatter view is used when no network is available. Evidence
  parsing itself never touches the network.
- **Not yet independently validated.** Exercised on one real extraction and a
  synthetic ground-truth dataset. Treat output as analytical until you have
  validated it against known-good data of your own.

## Architecture

```
paytmforensics/
├── core/         case lifecycle, models, integrity, audit log, case DB
├── ingest/       artifact discovery, read-only SQLite opener
├── parsers/      one module per domain (identity, transactions, …)
├── carving/      SQLite freelist / freeblock / slack carver + dedup
├── correlate/    entity resolution + master timeline build
├── enrich/       timestamps, enums, error codes, IFSC, VPA, protobuf, config
├── report/       HTML (Jinja2) + PDF + KML / GeoJSON / CSV / JSON exports
├── gui/          PySide6 app, theme, dashboard, table/chat/map/timeline views
├── resources/    bundled lookup tables (IFSC, error-mapper, leaflet, protos)
└── cli.py        headless entry point
```

The CLI and the GUI are thin shells over the same `Case` object in `core/case.py`,
so the pipeline is identical headless or interactive.

## Tests

```bash
# Full suite — uses a real extraction if PAYTM_EXTRACTION is set, otherwise
# skips the extraction-dependent tests.
pytest -q

# Synthetic-only (no real data needed):
pytest tests/test_m6_reference.py

# A single domain:
pytest tests/test_m11_config_explain.py -v
```

`tests/synthetic.py` builds a fabricated Paytm-shaped extraction with known
ground truth so the full pipeline can be regression-tested on CI without
distributing private data.

On a headless/CI box where QtWebEngine is installed but **non-functional**
(missing Chromium resource packs, no GPU/sandbox), constructing a WebEngine
view aborts the process. Set `PAYTM_NO_WEBMAP=1` to make the GUI map and the
HTML→PDF fallback skip WebEngine cleanly — the map uses its offline scatter
view and PDF falls back to WeasyPrint (or a clear error). Recommended for CI:

```bash
QT_QPA_PLATFORM=offscreen PAYTM_NO_WEBMAP=1 pytest -q
```

A few tests (in `test_rigorous.py`, `test_audit_fixes.py`, `test_m2.py`,
`test_m10_extras.py`) assert known PII values from a real extraction
(subject name / phone / customer-id / a specific RRN, etc.). Those values
are **not** committed. To run them, drop a `tests/_truth.json` (gitignored)
following the schema in `tests/_truth_example.json`; without it those
specific assertions are skipped.

## Packaging

A PyInstaller spec is included:

```bash
pyinstaller paytmforensics.spec
# → dist/PaytmForensics/PaytmForensics.exe   (Windows)
```

The spec bundles the GUI resources (Leaflet, IFSC table, error-mapper, etc.).

## Project status & roadmap

This is **WIP**. What's done and what's next:

**Done**
- M0–M5: core forensic pipeline, GUI, reporting, synthetic CI tests
- M6: PyInstaller packaging
- M7: WebView (cookies, storage, cache, service-worker)
- M8: chat view, map view, visual timeline
- M9: capabilities enumeration
- M10: PDF, KML/GeoJSON, global search, charts, IFSC, app-state, crash, protobuf
- M11: config / feature-flag humanisation

**Planned / under consideration**
- Linux-first GUI smoke tests in CI (currently Windows-developed)
- LevelDB parsing on Windows (currently `plyvel`-gated)
- Optional Android Keystore unwrap path when an examiner has a *live* device
  available (out of scope for offline analysis)
- More structured carving signatures for additional Paytm tables
- Court-format report template (cover page, exhibit list, examiner sign-off)
- Independent validation against a published reference dataset

Issues and PRs are welcome — please redact any real evidence before attaching
sample data.

## Disclaimer

This tool is provided **as-is, with no warranty**. It is intended for trained
digital-forensics practitioners working within their authority. Whether output
is admissible in your jurisdiction depends on your acquisition method, your
chain-of-custody handling, local rules of evidence, and an independent
validation of the tool against known-good data — none of which this README can
provide for you.

The author is not affiliated with Paytm / One97 Communications. All trademarks
belong to their respective owners.

See also: [`METHODOLOGY.md`](METHODOLOGY.md), [`docs/PRD.md`](docs/PRD.md),
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).
