# PaytmForensics — Methodology & Limitations

This document supports admissibility: it states exactly how the tool reads, decodes, carves,
and attributes data, and what it cannot do.

## 1. Evidence handling (read-only)
- A SQLite database with **no** `-wal` sidecar is opened directly with
  `file:<path>?mode=ro&immutable=1`. SQLite is forbidden from writing, journaling, or
  WAL-replaying against the source.
- A database **with** a populated `-wal` is copied (db + `-wal` + `-shm`) to a scratch
  directory and the *copy* is opened `mode=ro`, so the write-ahead log is applied and the
  true current state is read. `immutable=1` makes SQLite ignore the WAL entirely, which
  hides rows that exist only in the log and reports rows the log deleted as still live.
  The source is only ever read; its SHA-256 is captured before and after the copy and
  compared, and the scratch copy is destroyed. This is the "operate on a working copy"
  path sanctioned by EV-1. Every record records which mode was used in
  `provenance.read_mode`, and `wal_report.json` states per table how many rows each mode
  sees.
- At ingest the tool computes **SHA-256 and MD5** of every file in the extraction and records
  them in `manifest.json`. `--verify` re-hashes after processing and asserts no change.
- Validation test `test_D_source_byte_identical_after_run` proves the source is byte-identical
  (hash + size + mtime) before and after a full run.

## 2. Provenance
Every emitted record carries: `source_file`, `source_table`, `rowid` (or `byte_offset` for
carved data), `origin` (`live` | `carved`), `confidence`, the source file's `ingest_sha256`,
and `read_mode` (`immutable` | `wal_applied`). CSV exports surface all of these as columns;
the HTML report surfaces source file, table, row/offset, a truncated hash, read mode,
origin and confidence per record.

## 3. Decoding
- **Timestamps** are shown with the raw stored value, the detected epoch type
  (`unix_ms`, `unix_s`, `webkit_us`), and the decoded UTC ISO string. Epoch type is detected
  by magnitude; WebView cookie times are WebKit microseconds since 1601.
- **Enums** (`txnIndicator`, `statusKey`, `txnCategory`, WorkManager state) are mapped from
  tables derived from observed data and the decompiled app. Unknown codes are rendered as
  `code:<n>` — never guessed.
- **Error codes** are decoded from `error_mapper.json` bundled from the decompiled app.

## 4. Deleted-record carving
- The carver reads the DB file as bytes and operates ONLY on **unallocated regions**: freelist
  pages, intra-page freeblocks (deleted cells), and the gap between the cell-pointer array and
  the cell-content area. Live cells are never scanned, so live data is never re-reported.
- Two passes: (1) structured SQLite record reconstruction (serial-type header + body), and
  (2) a byte-pattern scan for identifiers (VPA/RRN/phone/txn-id) that recovers content from
  cells whose record header was clobbered by the freeblock header.
- **Every** SQLite database in the extraction is carved, and so is each `-wal`: a
  write-ahead log is a sequence of old page images and is the richest source of recently
  deleted rows. The same unallocated-region logic is applied per WAL frame page, so live
  cells are never scanned.
- Carved records are deduped against live identifiers and labelled `origin=carved` with a
  confidence < 1.0. Validated on synthetic insert→delete→carve datasets, including the
  `secure_delete=ON` case (content wiped → not recoverable), and proven free of false
  positives on a clean database.

## 5. Encrypted artifacts (NOT decrypted)
- `Data.xml`, `DataUPI.xml`, `DataERUPEE.xml` store secrets as **AES-256-GCM** values whose
  data key is wrapped with an **RSA-2048 (OAEP-SHA256)** key generated in and held by the
  **Android hardware keystore (TEE)**; the private key is non-exportable and absent from any
  filesystem extraction. These stores are also HMAC integrity-checked by the app.
- The encrypted `shared_jsons` caches (`TPAP_UPI.json`, `SMS_smssdk_pref.json`) are base64
  ciphertext whose key is not present in the extraction.
- The tool **catalogues** these (name, size, owning module, cipher, keystore alias, reason)
  and never attempts decryption. Offline decryption is not possible; it would require the
  original device with the app's runtime/keystore.

## 6. Secrets in plaintext stores
Token-bearing preference keys (e.g. `sso_token=`, `pb_auth_token`, `afUninstallToken`) are
**redacted to length-only** in output to avoid copying live credentials into a report.

## 6a. Presentation masking of PII / bank identifiers
An examiner can toggle **Hide sensitive data** in the GUI, or pass `--redact-report`, to
mask names, phone numbers, customer/device/Sendbird IDs, UPI IDs, account and IFSC
identifiers, RRNs and precise coordinates (reduced to ~1 decimal, i.e. city level).

This is a **display layer only** and is **off by default**:
- `case.db` always holds the complete unmasked evidence, so the case remains a faithful
  record and nothing about parsing changes.
- Amounts, dates, statuses, counts and provenance (including hashes) are never masked —
  they carry the analysis and are not identifying on their own.
- Filtering and search operate on the real values and only the *display* is masked, so a
  masked view remains fully searchable.
- Masks preserve length and structure (`••••••••••`, `••••@•••••`), and personal names
  keep each word's initial so two counterparties remain distinguishable.
- A masked HTML report carries a "REDACTED COPY" banner stating what was hidden.

## 7. Reproducibility
Given the same input and tool version, JSON exports and the HTML report body are deterministic
(stable ordering; the only varying field is the report-generation timestamp). Verified by
`test_E_canonical_export_stable` and `test_V_html_body_reproducible`.

## 8. Known limitations
- WebView LevelDB / IndexedDB and protobuf analytics payloads are best-effort (M7, optional).
- Carving recovery depends on `secure_delete`/`auto_vacuum`/checkpoint state of the source DBs;
  a vacuumed DB may yield no recoverable deleted records (reported honestly as 0).
- Audit logs can be verified by a third party with `AuditLog.verify(path)` or
  `python -m paytmforensics.cli --out <case> --verify-audit`.
- PDF output requires WeasyPrint + native libraries; otherwise generate HTML and print to PDF.
