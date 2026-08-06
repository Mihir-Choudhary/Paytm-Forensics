"""Parsers for artifacts that previously had no parser at all.

Closes these gaps found by the coverage audit:
  * FR-3  `contacts` database (contacts / contacts_phones / enrichment_data)
  * FR-9  `discoveryDb.TBL_REMINDERS` (recurring payments / reminders)
  * FR-8  `files/in_app_notification_model` (Java-serialized notification store)
  *       `RealtimeSmsUploadDb` (SMS upload evidence)
  *       `pai_signal` / `pai_push_signal` (signal events, same shape as bank_signal)
  * FR-1  the subject's linked bank account, as a real `account` record
"""
from __future__ import annotations

import json
import os
import re
from typing import Iterator

from .base import BaseParser, register
from ..core.models import (Person, Account, AppStateItem, LocationFix, Notification,
                           Record)
from ..ingest import sqlite_ro as sql
from ..enrich import timestamps, ifsc
from ..enrich.secrets import redact_text


# --------------------------------------------------------------------------- #
@register
class ContactsDbParser(BaseParser):
    """FR-3: the `contacts` database (distinct from chatDb.TBL_USERS)."""
    name = "contacts.db"
    needs = ("contacts",)

    def parse(self) -> Iterator[Record]:
        art = self.get("contacts")
        if not art:
            return
        with self.open(art) as con:
            tables = sql.list_tables(con)
            phones: dict = {}
            if "contacts_phones" in tables:
                for _rid, r in sql.rows(con, "contacts_phones"):
                    key = r.get("contact_id") or r.get("contactId") or r.get("id")
                    num = r.get("phone") or r.get("number") or r.get("phone_number")
                    if key is not None and num:
                        phones.setdefault(key, []).append(str(num))
            enrich: dict = {}
            if "enrichment_data" in tables:
                for _rid, r in sql.rows(con, "enrichment_data"):
                    key = r.get("contact_id") or r.get("id")
                    if key is not None:
                        enrich[key] = r
            main = next((t for t in ("contacts", "contact", "Contact") if t in tables), None)
            if not main:
                return
            for rowid, r in sql.rows(con, main):
                cid = r.get("id") or r.get("contact_id") or rowid
                nums = phones.get(cid) or [v for v in (r.get("phone"), r.get("number")) if v]
                e = enrich.get(cid) or {}
                yield Person(
                    provenance=self.prov(art, main, rowid),
                    raw={"contact": r, "enrichment": e or None},
                    customer_id=str(r.get("customer_id") or e.get("customer_id") or "") or None,
                    name=r.get("name") or r.get("display_name") or e.get("name"),
                    phone=(nums[0] if nums else None),
                    vpas=[v for v in [r.get("vpa"), e.get("vpa")] if v],
                    person_type=r.get("type") or "CONTACT",
                    verified_name=e.get("verified_name"),
                )


# --------------------------------------------------------------------------- #
@register
class RemindersParser(BaseParser):
    """FR-9: discoveryDb.TBL_REMINDERS — recurring payments / reminders."""
    name = "state.reminders"
    needs = ("discoveryDb.db",)

    def parse(self) -> Iterator[Record]:
        art = self.get("discoveryDb.db")
        if not art:
            return
        with self.open(art) as con:
            tables = [t for t in sql.list_tables(con) if "REMINDER" in t.upper()]
            for table in tables:
                for rowid, r in sql.rows(con, table):
                    ts = None
                    for k in ("reminderDate", "dueDate", "date", "timestamp", "createdAt"):
                        if r.get(k):
                            ts = timestamps.decode(r.get(k)).to_dict()
                            break
                    label = (r.get("title") or r.get("name") or r.get("categoryName")
                             or r.get("rechargeNumber") or "reminder")
                    amount = r.get("amount") or r.get("dueAmount")
                    yield AppStateItem(
                        provenance=self.prov(art, table, rowid),
                        raw=r,
                        key="reminder",
                        value=f"{label}" + (f" — {amount}" if amount else ""),
                        detail=r.get("deeplink") or r.get("url"),
                        timestamp=ts,
                    )


# --------------------------------------------------------------------------- #
@register
class SignalEventParser(BaseParser):
    """pai_signal / pai_push_signal — same SignalEventDb shape as bank_signal.

    bank_signal was the only one read, so location and behavioural events in the other two
    signal databases were dropped entirely.
    """
    name = "location.pai_signal"
    needs = ("pai_signal", "pai_push_signal")

    def parse(self) -> Iterator[Record]:
        for logical in ("pai_signal", "pai_push_signal"):
            art = self.get(logical)
            if not art:
                continue
            with self.open(art) as con:
                if "SignalEventDb" not in sql.list_tables(con):
                    continue
                for rowid, r in sql.rows(con, "SignalEventDb"):
                    ev = r.get("signalEvent")
                    if not ev:
                        continue
                    try:
                        outer = json.loads(ev)
                    except (ValueError, TypeError):
                        continue
                    if outer.get("eventType") != "location_event":
                        continue
                    payload = outer.get("payload")
                    try:
                        p = json.loads(payload) if isinstance(payload, str) else (payload or {})
                    except (ValueError, TypeError):
                        p = {}
                    lat, lon = p.get("latitude"), p.get("longitude")
                    if lat is None or lon is None:
                        continue
                    yield LocationFix(
                        provenance=self.prov(art, "SignalEventDb", rowid),
                        raw=p,
                        latitude=_f(lat), longitude=_f(lon), speed=_f(p.get("speed")),
                        source_kind=logical,
                        timestamp=timestamps.decode(r.get("deviceDateTime")).to_dict(),
                    )


# --------------------------------------------------------------------------- #
@register
class SmsUploadParser(BaseParser):
    """RealtimeSmsUploadDb — records of SMS the app queued for upload."""
    name = "sms.upload"
    needs = ("RealtimeSmsUploadDb",)

    def parse(self) -> Iterator[Record]:
        art = self.get("RealtimeSmsUploadDb")
        if not art:
            return
        with self.open(art) as con:
            for table in sql.list_tables(con):
                for rowid, r in sql.rows(con, table):
                    ts = None
                    for k in ("timestamp", "date", "createdAt", "smsTime"):
                        if r.get(k):
                            ts = timestamps.decode(r.get(k)).to_dict()
                            break
                    body = r.get("body") or r.get("message") or r.get("sms")
                    yield AppStateItem(
                        provenance=self.prov(art, table, rowid),
                        raw=r,
                        key="sms_upload",
                        value=(redact_text(str(body))[:300] if body else f"({table} row)"),
                        detail=r.get("sender") or r.get("address"),
                        timestamp=ts,
                    )


# --------------------------------------------------------------------------- #
_JAVA_STR = re.compile(rb"[\x20-\x7e]{6,200}")


@register
class InAppNotificationParser(BaseParser):
    """FR-8: files/in_app_notification_model — a Java-serialized notification store.

    Full Java deserialisation is out of scope; we surface the embedded JSON payload when
    present and otherwise a best-effort string inventory, clearly labelled.
    """
    name = "notifications.in_app"
    needs = ("in_app_notification_model",)

    def parse(self) -> Iterator[Record]:
        art = self.get("in_app_notification_model")
        if not art:
            return
        try:
            with open(art.abs_path, "rb") as f:
                raw = f.read()
        except OSError:
            return
        txt = raw.decode("utf-8", "ignore")
        emitted = 0
        dec = json.JSONDecoder()
        i = 0
        while True:
            i = txt.find("{", i)
            if i < 0:
                break
            try:
                obj, end = dec.raw_decode(txt[i:])
            except ValueError:
                i += 1
                continue
            if isinstance(obj, dict) and obj:
                yield Notification(
                    provenance=self.prov(art, None, confidence=0.7),
                    raw={"decoded": obj},
                    title=str(obj.get("title") or obj.get("heading") or "")[:200] or None,
                    message=redact_text(str(obj.get("message") or obj.get("body") or
                                            json.dumps(obj, ensure_ascii=False)))[:400],
                    deep_link=obj.get("deeplink") or obj.get("deepLink") or obj.get("url"),
                    campaign_id=str(obj.get("campaignId") or "") or None,
                )
                emitted += 1
            i += max(end, 1)
        if emitted:
            return
        # no embedded JSON: inventory the serialized strings so the artifact is not silent
        strings = [m.group(0).decode("ascii", "ignore") for m in _JAVA_STR.finditer(raw)]
        yield Notification(
            provenance=self.prov(art, None, confidence=0.4),
            raw={"strings": strings[:80]},
            title="(Java-serialized in-app notification store)",
            message=redact_text("best-effort string inventory: "
                                + " | ".join(strings[:12])),
        )


# --------------------------------------------------------------------------- #
@register
class SubjectAccountParser(BaseParser):
    """FR-1: the subject's linked bank account, as a first-class `account` record.

    The Account model existed but no parser ever emitted one, which is why the dashboard's
    subject card showed an empty Bank field. Derived from the passbook instrument rows.
    """
    name = "identity.account"
    needs = ("passbook.db",)

    def parse(self) -> Iterator[Record]:
        art = self.get("passbook.db")
        if not art:
            return
        seen: set = set()
        with self.open(art) as con:
            tables = sql.list_tables(con)
            if "UthInstrumentEntity" not in tables:
                return
            for rowid, r in sql.rows(con, "UthInstrumentEntity"):
                acct = r.get("identifier")
                if not acct or acct in seen:
                    continue
                seen.add(acct)
                bank, branch = ifsc.resolve(acct)
                yield Account(
                    provenance=self.prov(art, "UthInstrumentEntity", rowid),
                    raw=r,
                    bank_name=bank or r.get("instrumentName"),
                    ifsc_or_branch=acct,
                    account_type=r.get("accountType"),
                    masked_number=r.get("maskedAccountNumber") or r.get("maskedAccNo"),
                    instrument_type=_i(r.get("instrumentType")),
                )


def _f(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _i(v):
    try:
        return int(v)
    except (ValueError, TypeError):
        return None
