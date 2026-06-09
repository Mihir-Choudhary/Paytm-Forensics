"""Non-Qt data access for the GUI: reads the case DB, exposes domains/columns/filtering.

Kept Qt-free so it can be unit-tested headlessly and reused by the report/export layer.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .filters import FilterSpec, apply_filter, _all_strings, searchable_values

# preferred display columns per domain (others still available in the detail panel)
DISPLAY_COLUMNS = {
    "transaction": ["timestamp", "amount", "direction", "settled", "txn_source",
                    "counterparty_name", "counterparty_vpa", "counterparty_mobile", "rrn",
                    "account_used", "account_bank", "account_type", "status_label",
                    "category_label", "instrument", "narration"],
    "message": ["timestamp", "chat_with", "msg_type", "sender_name", "amount", "rrn", "content"],
    "person": ["name", "person_type", "phone", "vpas", "account_age_text", "bank_name"],
    "entity": ["names", "person_type", "phones", "vpas", "txn_count",
               "total_received", "total_paid", "msg_count", "is_subject"],
    "location": ["timestamp", "latitude", "longitude", "source_kind", "pincode"],
    "consent": ["timestamp", "consent_key", "consent_value", "synced_with_server"],
    "job": ["last_enqueue", "worker_class", "state_label", "interval_ms", "run_attempt_count"],
    "notification": ["timestamp", "title", "message", "deep_link", "campaign_id"],
    "search": ["timestamp", "query", "vertical_id", "url"],
    "config": ["key", "kind", "meaning", "value", "is_changed_from_default"],
    "diagnostic": ["timestamp", "event_type", "message", "flow_name", "network_type",
                   "network_carrier", "battery_pct", "process_state", "app_version"],
    "encrypted": ["name", "size", "owning_module", "cipher", "keystore_alias", "reason"],
    "pref": ["pref_file", "key", "value"],
    "carved": ["region", "page", "vpas", "rrns", "phones", "txn_ids", "confidence"],
    "timeline": ["utc_iso", "event_type", "summary", "ref_domain"],
    "cookie": ["host", "name", "value", "is_secure", "created", "expires"],
    "webstorage": ["store_kind", "origin", "key", "value", "best_effort"],
    "capability": ["category", "name", "available", "value"],
    "webcache": ["created", "url", "method", "user_id", "size", "body_preview"],
    "appstate": ["timestamp", "key", "value", "detail"],
    "crash": ["start_time", "session_id", "user_id", "preview"],
}

DOMAIN_LABELS = {
    "entity": "Entities (people)", "transaction": "Transactions", "message": "Chats",
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


class DataSource:
    def __init__(self, case_db_path: str):
        self.path = case_db_path
        # read-only: the GUI must never be able to mutate a case artifact after
        # its hashes were taken (also stops sqlite creating an empty DB on a bad path)
        uri = Path(case_db_path).resolve().as_uri() + "?mode=ro"
        try:
            self.con = sqlite3.connect(uri, uri=True)
        except sqlite3.OperationalError as e:
            raise sqlite3.OperationalError(
                f"cannot open case DB read-only: {case_db_path} ({e})") from e

    def domains(self) -> dict:
        cur = self.con.execute(
            "SELECT domain, COUNT(*) FROM records GROUP BY domain ORDER BY domain")
        return {r[0]: r[1] for r in cur.fetchall()}

    def load(self, domain: str) -> list[dict]:
        cur = self.con.execute(
            "SELECT data FROM records WHERE domain=? ORDER BY id", (domain,))
        return [json.loads(d) for (d,) in cur]

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
        return str(_fmt(rec.get(col)))

    def global_search(self, text: str, limit: int = 1000) -> list[tuple]:
        """Search every record's VALUES (not keys/provenance) across all domains, matching
        the per-table filter behaviour. Returns [(domain, summary, record)]."""
        text = (text or "").strip().lower()
        if not text:
            return []
        out = []
        for dom in self.domains():
            for r in self.load(dom):
                if any(text in s.lower() for s in _all_strings(searchable_values(r))):
                    out.append((dom, summarize(r, dom), r))
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
