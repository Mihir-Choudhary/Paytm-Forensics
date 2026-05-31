"""FR-1 Subject identity: device owner profile from prefs + chatDb + diagnostics."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Iterator

from .base import BaseParser, register
from ..core.models import Person, Record
from ..ingest import sqlite_ro as sql


def _read_prefs(path: str) -> dict:
    out = {}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return out
    for child in root:
        name = child.get("name")
        if not name:
            continue
        if child.tag == "string":
            out[name] = child.text
        else:
            out[name] = child.get("value")
    return out


@register
class IdentityParser(BaseParser):
    name = "identity"
    needs = ("bank_secure_prefs.xml", "chatDb.db")

    def parse(self) -> Iterator[Record]:
        # --- bank_secure_prefs.xml -> primary subject facts ---
        bs = self.get("bank_secure_prefs.xml")
        prefs = _read_prefs(bs.abs_path) if bs else {}
        customer_id = prefs.get("userId") or prefs.get("resId")
        phone = prefs.get("mobile")

        subject_name = None
        sendbird_id = None
        bank_name = None
        account_age = None

        chat = self.get("chatDb.db")
        if chat:
            with sql.open_ro(chat.abs_path) as con:
                if "TBL_USERS" in sql.list_tables(con):
                    for _rid, r in sql.rows(con, "TBL_USERS"):
                        if str(r.get("isMe")) == "1":
                            subject_name = r.get("sendbirdUserName") or r.get("name")
                            sendbird_id = r.get("sendbirdUserId")
                            phone = phone or r.get("phoneNumber")
                            bank_name = r.get("bankName") or r.get("user_bank_model_primaryBankName")
                            break

        art = bs or chat
        if not art:
            return
        yield Person(
            provenance=self.prov(art, "bank_secure_prefs/TBL_USERS"),
            raw={k: prefs.get(k) for k in ("userId", "mobile", "kyc_state",
                                            "ppb_bank_type", "is_upi_user", "acc_status")},
            customer_id=customer_id,
            name=subject_name,
            phone=phone,
            country_code="91" if phone else None,
            person_type="CUSTOMER",
            is_subject=True,
            account_age_text=account_age,
            sendbird_id=sendbird_id,
            bank_name=bank_name,
        )
