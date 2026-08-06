# Product Requirements Document (PRD)
## PaytmForensics — Offline Forensic Parser for the Paytm Android App

| | |
|---|---|
| **Product name** | PaytmForensics (working title) |
| **Document version** | 1.0 (draft) |
| **Date** | 2026-05-28 |
| **Author** | Forensics tooling team |
| **Status** | For review |
| **Classification** | Internal / Law-enforcement use |

---

## 1. Purpose

PaytmForensics is an **offline, read-only desktop application** that ingests a forensic
extraction of the Paytm Android app (`net.one97.paytm`) and produces a structured,
court-admissible analysis of the user's identity, financial transactions, communications,
location history, device behaviour, and recoverable deleted records.

It is built for **digital forensic examiners and law-enforcement investigators** who have
already acquired the app's data directory through a validated acquisition method and need to
analyse it in a defensible, reproducible way.

The tool does **not** connect to any network, does **not** acquire data from a live device,
and does **not** modify the source evidence.

---

## 2. Background

The Paytm app stores a large volume of forensically valuable data across ~20 SQLite databases,
an Android WorkManager database, WebView storage, ~45 shared-preference XML files, and a set of
cached JSON files. Prior manual analysis (see `db_analysis.txt`, `api_analysis.txt`,
`carve_results.txt`) established that:

- A single device snapshot exposes identity, full UPI transaction history (including NPCI RRNs),
  P2P chat/payment threads, GPS location history, contacts/counterparties, consents, and
  background-job history.
- **Freelist/slack/WAL carving can recover additional** transaction and counterparty
  records beyond the live rows. The yield depends entirely on the source's
  `secure_delete`/`auto_vacuum`/checkpoint state and is not promised as any multiple.
- Cross-database correlation is possible because **UPI VPA, customer ID, phone number, device
  ID, and Sendbird channel URL** act as shared join keys.
- A subset of stores (`Data.xml`, `DataUPI.xml`, `DataERUPEE.xml`, and the `shared_jsons`
  caches) are encrypted with an **AES-256-GCM key wrapped by an RSA key held in the Android
  hardware keystore (TEE)** and are therefore **not decryptable from a filesystem extraction**.

This PRD turns those findings into a product specification.

---

## 3. Goals and Non-Goals

### 3.1 Goals
- G1. Parse 100% of the **plaintext** artifacts in a Paytm extraction into a normalized model.
- G2. Recover deleted/residual records via SQLite freelist and slack-page carving.
- G3. Correlate artifacts across stores into unified entities (people, accounts, transactions, events).
- G4. Present results in a desktop GUI with **granular filtering, search, and a master timeline**.
- G5. Produce court-admissible outputs: HTML + PDF reports and JSON + CSV exports, each with
  full source attribution and integrity hashes.
- G6. Guarantee evidence integrity: read-only, hash-verified, audit-logged, reproducible.
- G7. Clearly document encrypted/unrecoverable artifacts rather than silently dropping them.

### 3.2 Non-Goals
- N1. **No data acquisition** from a device (no ADB/root/backup extraction). Input is a folder.
- N2. **No network activity** of any kind (no live API enrichment, no token replay). This is
  both a legal and an evidentiary requirement.
- N3. **No decryption of TEE-bound artifacts** (`Data*.xml`, encrypted `shared_jsons`). They are
  catalogued and flagged, not cracked.
- N4. No modification, "cleanup", or re-writing of source files.
- N5. Not a real-time monitoring or interception tool.

---

## 4. Target Users & Use Cases

| User | Use case |
|---|---|
| LEA investigator | Reconstruct a suspect's UPI payment activity and counterparties for a fraud/financial-crime case |
| Forensic examiner | Produce a defensible report with provenance for every datum, suitable for disclosure/court |
| Financial-crime analyst | Triage device data quickly, then deep-dive on transactions and chat threads |
| Defence/independent expert | Independently reproduce another examiner's findings from the same extraction |

**Primary use case:** Given an extracted `net.one97.paytm` folder, generate a complete,
attributable, court-ready report of the user's financial and communication footprint, including
recoverable deleted records, with zero alteration of the source.

---

## 5. Scope

### 5.1 In scope (input artifacts)

**Live SQLite databases (`/databases`):**
`AppLocale.db`, `PaytmMessageDatabase`, `RealtimeSmsUploadDb`, `appManagerDB`,
`bank_app_manager_database`, `bank_signal`, `cache_database`, `chatDb.db`,
`com.google.android.datatransport.events`, `contacts`, `discoveryDb.db`,
`google_app_measurement_local.db`, `pai_push_signal`, `pai_signal`, `passbook.db`,
`paytm_error_analytics`, `paytmbank_error_analytics`, `search_db`, `storefront_db_try3`,
`ups_database`.

**Additional databases:**
`no_backup/androidx.work.workdb` (WorkManager), `app_webview/Default/Cookies`,
`app_webview/Default/Web Data`.

**Key-value / XML / JSON stores:**
`shared_prefs/*.xml` (~45 files), `files/datastore/*`, `files/in_app_notification_model`,
`files/*PersistedInstallation*.json`, `files/AppEventsLogger.persistedevents`.

**WebView storage:**
`app_webview/Default/Local Storage/leveldb`, `Session Storage`, `IndexedDB` (best-effort LevelDB parse).

**Cached server responses (catalogued; decryption out of scope):**
`shared_jsons/*.json` (29 files incl. `TPAP_UPI.json`, `HOME.json`, `SMS_smssdk_pref.json`).

**Reference decode tables (bundled from decompiled app, read-only):**
`error_mapper.json` (error-code → message), default `bankappmanager` config, `.proto` schemas
for protobuf-encoded analytics payloads.

### 5.2 Out of scope
- TEE/hardware-keystore-bound decryption (documented as a known limitation).
- Live device acquisition and any network communication.
- Non-Paytm apps (architecture should not preclude future plugins, but only Paytm is in scope v1).

---

## 6. Functional Requirements — Parsers (by artifact domain)

Each parser MUST attach to every output record: **source file, table/key, rowid or byte offset,
and a record-origin flag (`live` vs `carved`)**.

### FR-1 Subject Identity
- Extract device owner: customer ID, name, phone, country code, KYC state, linked bank account
  (IFSC/branch prefix), bank type, device ID/model, install date, account-age text.
- Sources: `bank_secure_prefs.xml`, `chatDb.TBL_USERS (isMe=1)`, `appsflyer-data.xml`,
  `paytmbank_error_analytics`, `appManagerDB`, FCM/Firebase IDs.

### FR-2 Transactions (Passbook + UPI)
- Parse `passbook.UthListingEntity` + `UthInstrumentEntity`: amount, direction (credit/debit),
  counterparty VPA, merchant name, MCC/category, tag, narration, status, timestamp, instrument
  (bank account used).
- Parse payment messages in `chatDb.ChatMessageEntity.data`: amount, **RRN (NPCI reference)**,
  status, payment mode, deeplink, unique key.
- Decode integer enums (`txnIndicator`, `statusKey`, `txnCategory`, `errorCode`) using bundled
  decode tables; show both raw code and decoded label.

### FR-3 Contacts & Counterparties
- Parse `chatDb.TBL_USERS`: name, phone, VPA, type (customer/merchant), account-age, payment
  status flags, cached `getInfo`/`getPaymentInfo` responses.
- Parse `contacts`, `contacts_phones`, `enrichment_data` (when present).
- Parse `cache_database.cache_table`: VPA → verified merchant name / MCC.

### FR-4 Communications (Chat threads)
- Reconstruct conversations from `TBL_CHANNELS` + `ChatMessageEntity` +
  `DBChannelUserEntryCrossRef`, ordered via `TBL_MESSAGE_HISTORY`.
- Render each thread with participants, message previews, payment events, timestamps.
- Flag encrypted message BLOBs (`rawMessage`, `sender`) as present-but-unreadable.

### FR-5 Location History
- Parse GPS events from `bank_signal.SignalEventDb` JSON (lat/long/speed + timestamp + type).
- Parse `app_webview` cookies `lat`/`long`/`enteredPincode`.
- Cross-validate and de-duplicate coincident fixes from different sources; map view + export.

### FR-6 Consents & Permissions
- Parse `ups_database.ConsentTable`: SMS read, WhatsApp, contact sync, security shield, with
  grant timestamps and server-sync status.

### FR-7 Device Behaviour / Background Jobs
- Parse `androidx.work.workdb` (incl. its uncheckpointed WAL): worker class, state, last-enqueue
  time, interval, run count → behaviour timeline (SMS scraping, contact sync, signal upload).

### FR-8 Notifications & Messaging
- Parse `PaytmMessageDatabase` (`NotificationData`, `PushData`), `files/in_app_notification_model`,
  FCM tokens from `com.google.android.gms.appid.xml`.

### FR-9 Searches & App State
- Parse `search_db.recent_Search_Tbl`, `storefront_db_try3.sf_v_cache_table` (feature flags),
  `discoveryDb.TBL_REMINDERS` (recurring payments).

### FR-10 Configuration & Diagnostics
- Parse `appManagerDB`, `bank_app_manager_database` (feature flags, endpoints), diff against
  bundled defaults to highlight changed values.
- Parse `paytm_error_analytics` / `paytmbank_error_analytics`: sessions, app version, network,
  battery, decoded error codes.

### FR-11 Deleted-Record Recovery (Carving)
- For every SQLite DB: parse the freelist and scan unallocated/slack page space; carve residual
  rows (especially transactions, RRNs, phones, VPAs, chat previews).
- De-duplicate carved vs live; clearly label provenance and confidence.

### FR-12 Encrypted-Artifact Catalogue
- Enumerate `Data*.xml`, encrypted `shared_jsons`, encrypted message BLOBs; record presence,
  size, owning module, and the reason they are not decryptable (TEE-bound), with the exact
  keystore alias / cipher where known.

### FR-13 Cross-Artifact Correlation
- Build unified entities by joining on **VPA, customer ID, phone, device ID, channel URL**.
- Produce a per-counterparty profile aggregating all transactions, chats, and identifiers.

### FR-14 Master Timeline
- Merge every timestamped event (transactions, messages, locations, jobs, sessions, consents,
  searches, notifications) into one chronological timeline with filtering.

---

## 7. Functional Requirements — Application (GUI)

### FR-G1 Case management
- Create/open a "case": select extraction folder, enter case ID, examiner name, evidence number,
  notes. Persist a case file.

### FR-G2 Navigation
- Left-nav by domain (Identity, Transactions, Contacts, Chats, Location, Timeline, Jobs, Consents,
  Config, Diagnostics, Encrypted items, Carved/Deleted).

### FR-G3 Granular filtering (a primary requirement)
Every data grid MUST support filtering by, at minimum:
- Date/time range (with timezone selector).
- Free-text search across all string fields.
- Amount range and direction (credit/debit) for transactions.
- Counterparty (name / VPA / phone / merchant).
- Source artifact (which DB/file).
- Record origin (`live` vs `carved`).
- Status / category / tag.
- Combine filters (AND) and save filter presets per case.

### FR-G4 Detail & provenance view
- Clicking any record shows full field list + **provenance panel** (source file, table, rowid/offset,
  ingest hash, live/carved).

### FR-G5 Map view
- Plot location fixes on an offline map (bundled tiles or coordinate list if offline tiles unavailable),
  filterable by time and source.

### FR-G6 Relationship view
- Per-counterparty aggregated profile and a simple entity graph (subject ↔ counterparties).

### FR-G7 Reporting & export
- Generate HTML and PDF reports (full or filtered-subset), and JSON + CSV exports of any grid.
- Reports embed case metadata, tool version, input manifest hashes, and per-section source
  attribution. Report file itself is hashed on output.

### FR-G8 Audit & integrity surface
- Show input-manifest hashes and verification status; expose the audit log; warn loudly if any
  source hash mismatches a prior run.

---

## 8. Forensic / Evidentiary Requirements (court-admissibility)

| ID | Requirement |
|---|---|
| EV-1 | **Read-only by construction.** Open every SQLite DB via `file:...?mode=ro&immutable=1`; never create journal/WAL on source; operate on a working copy when any tooling could write. |
| EV-2 | **Integrity hashing.** Compute SHA-256 (and MD5 for legacy cross-checks) of every input file at ingest; store in a signed manifest; re-verify on each run. |
| EV-3 | **Source attribution.** Every output datum is traceable to file + table/key + rowid/offset + live/carved flag. |
| EV-4 | **Chain of custody.** Capture case ID, examiner, evidence number, acquisition notes, ingest time, tool + parser versions; include in every report. |
| EV-5 | **Audit log.** Append-only log of every action (open, parse, carve, filter, export) with timestamps; exportable. |
| EV-6 | **Reproducibility.** Same input + same tool version ⇒ byte-identical JSON export (stable ordering, explicit timezone, no run-dependent fields). |
| EV-7 | **Timestamp transparency.** Show raw stored value, detected epoch type (Unix ms, WebKit/Chrome µs-since-1601), and decoded UTC + examiner-selected local time. |
| EV-8 | **Honest limitations.** Encrypted/uncertain artifacts are reported as such with reasons; carved records are labelled with confidence and never presented as confirmed live data. |
| EV-9 | **Validation.** Ship a known/synthetic reference dataset with expected outputs; regression-test every release against it. |
| EV-10 | **No network.** Build with no network calls; document and (where feasible) enforce offline operation. |

---

## 9. Non-Functional Requirements

- **NFR-1 Platform:** Windows primary (examiner workstations); cross-platform where Python/Qt allows.
- **NFR-2 Performance:** Parse a typical extraction (~hundreds of MB) in < 2 min; carving in < 5 min;
  responsive GUI on 100k+ row grids (virtualized tables, indexed search).
- **NFR-3 Reliability:** A corrupt or partial artifact must not crash the run; log and continue.
- **NFR-4 Security:** No telemetry, no auto-update phone-home; all bundled reference data shipped locally.
- **NFR-5 Maintainability:** Parser-per-artifact plugin architecture; adding a new artifact = one module.
- **NFR-6 Packaging:** Single installable (PyInstaller) with bundled decode tables and dependencies.
- **NFR-7 Internationalisation of data:** Correctly handle UTF-8, emoji, and Indian-language strings
  (the locale DB and chat content contain them).

---

## 10. Outputs

- **HTML report** — self-contained, navigable, with embedded provenance and case metadata.
- **PDF report** — print/disclosure-ready rendering of the HTML.
- **JSON export** — full normalized model, stable-ordered, for ingestion by other tools.
- **CSV export** — per-domain flat tables (transactions, contacts, messages, locations, timeline).
- **Manifest** — input file list with hashes, tool version, parser versions, run metadata.

---

## 11. Assumptions & Constraints
- Input is a faithful, complete copy of the app's private data directory.
- Examiner performs acquisition and validates it independently (outside this tool).
- TEE-bound secrets are unrecoverable offline; the tool will not attempt to break them.
- LevelDB (WebView Local Storage) and protobuf payloads are parsed best-effort; partial results
  are labelled as such.

---

## 12. Success Metrics
- 100% of plaintext artifacts in the reference extraction parsed without error.
- Carving is exercised over every database and every `-wal`, and its yield is reported
  honestly (including zero) rather than promised as a multiple of the live-row count.
- Every report datum has complete, correct provenance (validated against reference dataset).
- Reproducibility check passes (identical JSON across runs).
- Examiner can produce a filtered court report end-to-end in < 10 minutes.

---

## 13. Risks
| Risk | Mitigation |
|---|---|
| Schema drift across Paytm versions | Version-tolerant parsers (read by column name; tolerate missing columns); record observed schema hash |
| False positives in carving | Strict record validation, confidence scoring, clear live/carved labelling |
| Mis-decoded timestamps | Explicit epoch detection + show raw value always |
| LevelDB/protobuf parsing gaps | Best-effort + labelled; never block the run |
| Admissibility challenge | EV-1..EV-10 controls + documented methodology + reference-dataset validation |

---

## 14. Roadmap (post-v1)
- Optional acquisition helper module (ADB/backup) as a separate, clearly-bounded component.
- Pluggable support for other UPI apps (PhonePe, GPay) reusing the correlation/timeline core.
- On-device decryption companion (Frida-based) for lawful live-device workflows.
- Automated counterparty risk scoring from transaction graphs.
