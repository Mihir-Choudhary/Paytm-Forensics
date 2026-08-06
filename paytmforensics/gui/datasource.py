"""Non-Qt data access for the GUI: reads the case DB, exposes domains/columns/filtering.

Kept Qt-free so it can be unit-tested headlessly and reused by the report/export layer.
"""
from __future__ import annotations

import json
import sqlite3

from .filters import FilterSpec, apply_filter

# preferred display columns per domain (others still available in the detail panel)
DISPLAY_COLUMNS = {
    "transaction": ["timestamp", "amount", "direction", "settled", "txn_source",
                    "counterparty_name", "counterparty_vpa", "counterparty_mobile", "rrn",
                    "account_used", "account_bank", "account_branch", "account_type",
                    "status_label", "category_label", "tag", "instrument", "payment_mode",
                    "error_code", "error_message", "note", "narration",
                    "txn_id", "source_txn_id"],
    "account": ["bank_name", "ifsc_or_branch", "account_type", "masked_number",
                "instrument_type"],
    "channel": ["timestamp", "name", "channel_url", "member_count", "message_count",
                "has_messages"],
    "message": ["timestamp", "chat_with", "msg_type", "sender_name", "sender_id", "amount",
                "rrn", "status", "encrypted_blob_present", "content", "channel_url"],
    "person": ["name", "person_type", "customer_id", "phone", "vpas", "sendbird_id",
               "verified_name", "masked_account", "account_age_text", "bank_name",
               "country_code", "is_subject"],
    "entity": ["names", "person_type", "phones", "vpas", "customer_ids", "sendbird_ids",
               "txn_count", "total_received", "total_paid", "msg_count",
               "account_age_text", "is_subject", "source_files"],
    "location": ["timestamp", "latitude", "longitude", "speed", "source_kind", "pincode"],
    "consent": ["timestamp", "consent_key", "consent_value", "synced_with_server"],
    "job": ["last_enqueue", "job_names", "worker_class", "tags", "state", "state_label",
            "interval_ms", "run_attempt_count"],
    "notification": ["timestamp", "title", "message", "deep_link", "campaign_id", "push_id"],
    "search": ["timestamp", "query", "vertical_id", "url"],
    "config": ["key", "kind", "meaning", "value", "is_changed_from_default"],
    "diagnostic": ["timestamp", "event_type", "message", "flow_name", "screen_name",
                   "error_code", "error_message", "latitude", "longitude",
                   "network_type", "network_carrier", "battery_pct", "process_state",
                   "is_rooted", "app_version", "customer_id", "device_id", "session_id"],
    "encrypted": ["name", "size", "owning_module", "cipher", "keystore_alias", "reason"],
    "pref": ["pref_file", "key", "value"],
    "carved": ["region", "page", "vpas", "rrns", "phones", "txn_ids", "confidence"],
    "timeline": ["utc_iso", "event_type", "summary", "ref_domain"],
    "cookie": ["host", "name", "value", "is_secure", "is_httponly", "created", "expires"],
    "webstorage": ["store_kind", "origin", "key", "value", "best_effort"],
    "capability": ["category", "name", "available", "value"],
    "webcache": ["created", "url", "method", "user_id", "size", "body_preview"],
    "appstate": ["timestamp", "key", "value", "detail"],
    "crash": ["start_time", "session_id", "user_id", "preview"],
}

DOMAIN_LABELS = {
    "entity": "Entities (people)", "transaction": "Transactions", "message": "Chats",
    "channel": "Conversations", "account": "Bank accounts",
    "person": "Contacts (raw)", "location": "Location", "timeline": "Timeline",
    "job": "Background jobs", "consent": "Consents", "notification": "Notifications",
    "search": "Searches", "config": "Config / flags", "diagnostic": "Diagnostics",
    "encrypted": "Encrypted artifacts", "pref": "Preferences", "carved": "Carved / deleted",
    "cookie": "WebView cookies", "webstorage": "WebView storage",
    "map": "Location map", "capability": "Capabilities",
    "webcache": "WebView cache", "appstate": "App state / cart", "crash": "Crash sessions",
}


import re as _re
_ISO_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def _friendly_dt(iso: str) -> str:
    """'2026-05-12T07:15:03.930000+00:00' -> '12 May 2026, 07:15:03'."""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%d %b %Y, %H:%M:%S")
    except Exception:
        return iso.replace("T", " ")[:19]


def _fmt(v):
    if isinstance(v, dict):
        iso = v.get("utc_iso")
        if iso:
            return _friendly_dt(iso)
        return v.get("raw") or ""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, str) and _ISO_RE.match(v):     # plain ISO strings (e.g. timeline)
        return _friendly_dt(v)
    return "" if v is None else v


class CaseOpenError(Exception):
    """The path given is not a usable PaytmForensics case. Carries an actionable message."""


class DataSource:
    def __init__(self, case_db_path: str, *, mask_sensitive: bool = False):
        self.path = case_db_path
        import os as _os
        if not _os.path.isfile(case_db_path):
            raise CaseOpenError(
                f"No case database found at:\n{case_db_path}\n\n"
                "Open a case directory produced by this tool (it contains case.db, "
                "manifest.json, audit.log and case_meta.json), or build one first:\n"
                "  python -m paytmforensics.cli --extraction <folder> --out <case_dir>")
        try:
            self.con = sqlite3.connect(case_db_path)
            self.con.execute("SELECT COUNT(*) FROM records").fetchone()
        except sqlite3.DatabaseError as e:
            raise CaseOpenError(
                f"{case_db_path}\n\nis not a readable case database ({e}).\n"
                "The file may be corrupt or truncated, or may belong to another tool. "
                "Rebuild the case from the original extraction.") from e
        #: presentation-only masking of PII / bank identifiers. OFF by default: the tool
        #: shows everything unless the examiner turns it on. Never changes case.db.
        self.mask_sensitive = mask_sensitive

    def set_mask(self, on: bool) -> None:
        self.mask_sensitive = bool(on)

    def known_names(self) -> set:
        """Real personal / merchant names in this case, for literal masking in free text.

        Names have no distinguishing shape, so pattern matching cannot find them inside an
        embedded-JSON blob; this closes that gap.
        """
        if getattr(self, "_names", None) is None:
            names = set()
            for dom, field in (("person", "name"), ("person", "verified_name"),
                               ("entity", "names"), ("message", "sender_name"),
                               ("message", "chat_with"),
                               ("transaction", "counterparty_name")):
                for r in self.load(dom):
                    v = r.get(field)
                    for n in (v if isinstance(v, list) else [v]):
                        if isinstance(n, str) and len(n) > 2:
                            names.add(n)
            self._names = names
        return self._names

    def mask(self, rec: dict) -> dict:
        """Masked copy of a record when masking is on, else the record itself."""
        if not self.mask_sensitive:
            return rec
        from ..core import privacy
        return privacy.mask_record(rec, self.known_names())

    def domains(self) -> dict:
        try:
            cur = self.con.execute(
                "SELECT domain, COUNT(*) FROM records GROUP BY domain ORDER BY domain")
            return {r[0]: r[1] for r in cur.fetchall()}
        except sqlite3.DatabaseError:
            return {}

    def load_shown(self, domain: str) -> list[dict]:
        """Records as they should be DISPLAYED (masked when masking is on).

        Views that bake text at construction time must use this rather than load(),
        otherwise the masking toggle would silently miss them.
        """
        recs = self.load(domain)
        return [self.mask(r) for r in recs] if self.mask_sensitive else recs

    def load(self, domain: str) -> list[dict]:
        try:
            cur = self.con.execute(
                "SELECT data FROM records WHERE domain=? ORDER BY id", (domain,))
            return [json.loads(d) for (d,) in cur]
        except (sqlite3.DatabaseError, ValueError):
            return []

    def columns(self, domain: str) -> list[str]:
        cols = DISPLAY_COLUMNS.get(domain)
        if cols:
            return cols
        recs = self.load(domain)
        keys = []
        for r in recs[:20]:
            for k in r:
                if k not in ("provenance", "raw", "domain") and k not in keys:
                    keys.append(k)
        return keys

    def rows(self, domain: str, spec: FilterSpec | None = None) -> list[dict]:
        recs = self.load(domain)
        if spec:
            recs = apply_filter(recs, spec)
        return recs

    def cell(self, rec: dict, col: str) -> str:
        v = rec.get(col)
        if self.mask_sensitive:
            from ..core import privacy
            v = privacy.mask_field(col, v, self.known_names())
        return str(_fmt(v))

    def global_search(self, text: str, limit: int = 1000) -> list[tuple]:
        """Search every record's VALUES (not keys/provenance) across all domains, matching
        the per-table filter behaviour. Returns [(domain, summary, record)]."""
        from .filters import _all_strings
        text = (text or "").strip().lower()
        if not text:
            return []
        out = []
        for dom in self.domains():
            for r in self.load(dom):
                searchable = {k: v for k, v in r.items()
                              if k not in ("provenance", "raw", "domain")}
                if any(text in s.lower() for s in _all_strings(searchable)):
                    # search the REAL values, display a masked copy
                    shown = self.mask(r)
                    out.append((dom, summarize(shown, dom), shown))
                    if len(out) >= limit:
                        return out
        return out

    def close(self):
        self.con.close()


def summarize(r: dict, dom: str) -> str:
    """A short human summary of a record for search results."""
    for keys in (("amount", "direction", "counterparty_name"),
                 ("content", "chat_with"), ("name", "phone"), ("names",),
                 ("title", "message"), ("query",), ("consent_key", "consent_value"),
                 ("worker_class", "state_label"), ("key", "value"),
                 ("category", "name"), ("latitude", "longitude"),
                 ("event_type", "message"), ("summary",), ("host", "name")):
        vals = [str(_fmt(r.get(k))) for k in keys if r.get(k) not in (None, "", [])]
        if vals:
            return " · ".join(vals)[:120]
    return dom
