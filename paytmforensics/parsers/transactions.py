"""FR-2 Transactions: passbook ledger + UPI payment messages (incl. NPCI RRN)."""
from __future__ import annotations

import json
import re
from typing import Iterator

_RRN_RE = re.compile(r"^\d{12}$")
_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")

from .base import BaseParser, register
from ..core.models import Transaction, Record
from ..ingest import sqlite_ro as sql
from ..enrich import enums, errorcodes, timestamps, ifsc, vpa as vpamod


@register
class PassbookParser(BaseParser):
    name = "transactions.passbook"
    needs = ("passbook.db",)

    def parse(self) -> Iterator[Record]:
        art = self.get("passbook.db")
        if not art:
            return
        with sql.open_ro(art.abs_path) as con:
            tables = sql.list_tables(con)
            # instruments keyed by sourceTxnId for join
            instruments: dict[str, dict] = {}
            if "UthInstrumentEntity" in tables:
                for _rid, r in sql.rows(con, "UthInstrumentEntity"):
                    stid = r.get("sourceTxnId")
                    if stid:
                        instruments.setdefault(stid, r)
            if "UthListingEntity" not in tables:
                return
            for rowid, r in sql.rows(con, "UthListingEntity"):
                stid = r.get("sourceTxnId")
                inst = instruments.get(stid, {})
                amount = _to_float(r.get("amount"))
                ec = r.get("errorCode")
                acct_id, acct_type = _instrument_account(r.get("userInstrumentInfo"), inst)
                acct_bank, acct_branch = ifsc.resolve(acct_id)
                ss = r.get("searchableStrings")
                t = Transaction(
                    provenance=self.prov(art, "UthListingEntity", rowid),
                    raw=r,
                    txn_id=r.get("txnId"),
                    source_txn_id=stid,
                    amount=amount,
                    direction=enums.txn_direction(r.get("txnIndicator")),
                    counterparty_vpa=r.get("identifier"),
                    counterparty_name=_second_party_name(r.get("secondPartyInfo")),
                    counterparty_mobile=r.get("mobileNumber") or _searchable_mobile(ss),
                    rrn=_searchable_rrn(ss),                 # verified: last token == NPCI RRN
                    account_used=acct_id,
                    account_type=acct_type,
                    account_bank=acct_bank,
                    account_branch=acct_branch,
                    payment_mode=r.get("streamSource"),
                    status_raw=r.get("statusKey"),
                    status_label=enums.txn_status(r.get("statusKey")),
                    category_raw=r.get("txnCategory"),
                    category_label=enums.txn_category(r.get("txnCategory")),
                    tag=r.get("txnTag"),
                    narration=r.get("narration") or r.get("remarks"),
                    instrument=inst.get("instrumentName") or _instrument_name(r.get("userInstrumentInfo")),
                    error_code=str(ec) if ec not in (None, "") else None,
                    error_message=errorcodes.message(ec),
                    settled=(str(r.get("statusKey")) == "2"),   # 2 = success
                    txn_source="passbook",
                    timestamp=timestamps.decode(r.get("txnDate")).to_dict(),
                )
                yield t


@register
class ChatLedgerParser(BaseParser):
    """Payments evidenced in chat that are NOT in the passbook ledger.

    chatDb stores the full payment payload (uniqueKey == passbook sourceTxnId, RRN, amount,
    status, instrument, txnDate). We emit these as transactions but DEDUPE by uniqueKey
    against the passbook ledger so a payment recorded in both sources is counted once
    (passbook wins). Declined/failed/request events are kept but marked settled=False.
    """
    name = "transactions.chat"
    needs = ("chatDb.db",)

    def parse(self) -> Iterator[Record]:
        art = self.get("chatDb.db")
        if not art:
            return
        # passbook sourceTxnIds for dedup (passbook is authoritative)
        pb_ids: set = set()
        pb = self.get("passbook.db")
        if pb:
            with sql.open_ro(pb.abs_path) as c:
                if "UthListingEntity" in sql.list_tables(c):
                    for _rid, rr in sql.rows(c, "UthListingEntity"):
                        if rr.get("sourceTxnId"):
                            pb_ids.add(rr.get("sourceTxnId"))

        with sql.open_ro(art.abs_path) as con:
            tables = sql.list_tables(con)
            if "ChatMessageEntity" not in tables:
                return
            subject_sb = None
            if "TBL_USERS" in tables:
                for _rid, u in sql.rows(con, "TBL_USERS"):
                    if str(u.get("isMe")) == "1":
                        subject_sb = u.get("sendbirdUserId")
                        break
            party = _channel_counterparties(con, tables)

            for rowid, r in sql.rows(con, "ChatMessageEntity"):
                data = r.get("data")
                if not data:
                    continue
                try:
                    d = json.loads(data)
                except (ValueError, TypeError):
                    continue
                amt = _to_float(d.get("amount") or d.get("displayAmount"))
                if amt is None:
                    continue                       # not a payment message
                uniq = d.get("uniqueKey")
                if uniq and uniq in pb_ids:
                    continue                       # already in passbook ledger (dedup)
                ctype = (r.get("customType") or "").upper()
                status = (d.get("msgStatus") or d.get("r_sts") or "").upper()
                is_request = "REQUEST" in ctype and "RESPONSE" not in ctype
                if is_request:
                    direction = None               # a request, not a money movement
                elif subject_sb and r.get("senderId") == subject_sb:
                    direction = "debit"            # subject sent the money
                else:
                    direction = "credit"           # subject received the money
                settled = (status == "SUCCESS") and not is_request and "FAIL" not in ctype
                cp = party.get(r.get("channelUrl"))
                cp_name = cp[0] if cp else r.get("senderName")
                cp_phone = cp[1] if cp else None
                yield Transaction(
                    provenance=self.prov(art, "ChatMessageEntity", rowid),
                    raw={"data": d, "messageContent": r.get("messageContent")},
                    txn_id=d.get("txnId"),
                    source_txn_id=uniq,
                    amount=amt,
                    direction=direction,
                    counterparty_name=cp_name,
                    counterparty_mobile=cp_phone,
                    rrn=d.get("rrn"),
                    payment_mode=d.get("paymentMode"),
                    status_label=status or None,
                    narration=r.get("messageContent"),
                    note=(d.get("note") or None),
                    # NOTE: chat `inst` is a display blob (icons/labels), not a clean
                    # instrument name — passbook holds the authoritative instrument, so we
                    # leave it unset here rather than store noise.
                    settled=settled,
                    txn_source="chat",
                    # use createdAt (unix-ms UTC); data.txnDate is a local-time STRING (IST)
                    timestamp=timestamps.decode(r.get("createdAt")).to_dict(),
                )


def _channel_counterparties(con, tables) -> dict:
    """channelUrl -> (counterparty display name, phone) for the non-subject member."""
    if not ({"DBChannelUserEntryCrossRef", "TBL_USERS"} <= set(tables)):
        return {}
    users = {}
    for _rid, u in sql.rows(con, "TBL_USERS"):
        users[u.get("userPrimaryKey")] = (u.get("sendbirdUserName") or u.get("name"),
                                          u.get("phoneNumber"), str(u.get("isMe")) == "1")
    out: dict = {}
    for _rid, x in sql.rows(con, "DBChannelUserEntryCrossRef"):
        chan = x.get("channelUrl")
        name, phone, is_me = users.get(x.get("userPrimaryKey"), (None, None, False))
        if chan and name and not is_me:
            out.setdefault(chan, (name, phone))
    return out


def _searchable_rrn(ss):
    """passbook searchableStrings ends with the 12-digit NPCI RRN (verified)."""
    if not ss:
        return None
    last = str(ss).split(",")[-1].strip()
    return last if _RRN_RE.match(last) else None


def _searchable_mobile(ss):
    """extract a 10-digit Indian mobile token from searchableStrings (P2P counterparties)."""
    if not ss:
        return None
    for tok in str(ss).split(","):
        tok = tok.strip()
        if _MOBILE_RE.match(tok):
            return tok
    return None


def _instrument_account(uii_json, inst_row):
    """Return (account identifier, accountType) of the instrument the subject used."""
    # prefer the joined UthInstrumentEntity row
    if inst_row and inst_row.get("identifier"):
        return inst_row.get("identifier"), inst_row.get("accountType")
    if uii_json:
        try:
            arr = json.loads(uii_json)
            if isinstance(arr, list) and arr:
                return arr[0].get("identifier"), arr[0].get("accountType")
        except (ValueError, TypeError):
            pass
    return None, None


# NOTE: chat payment events are intentionally NOT emitted as `transaction` records.
# They are captured by the chat parser as `message` records (with amount, RRN, status,
# and sender), which avoids double-counting the same payment in two domains and keeps the
# `transaction` domain equal to the authoritative passbook ledger. See chats.py.


def _to_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _second_party_name(s):
    if not s:
        return None
    try:
        return json.loads(s).get("name")
    except (ValueError, TypeError):
        return None


def _instrument_name(s):
    if not s:
        return None
    try:
        arr = json.loads(s)
        if isinstance(arr, list) and arr:
            return arr[0].get("instrumentName")
    except (ValueError, TypeError):
        return None
    return None
