"""Pure-Python entity↔record attribution (no Qt) — unit-testable headlessly.

Backs the entity drill-down pivot: given one resolved entity (the 'entity'
domain record produced by correlate/entities.py), find every transaction and
message that involves that counterparty. Matching mirrors the correlation
rules (VPA, name, sendbird id) and adds phone matching, normalised to the
last 10 digits so "+91XXXXXXXXXX" and "XXXXXXXXXX" compare equal.
"""
from __future__ import annotations


def _norm_phone(p: str | None) -> str:
    digits = "".join(ch for ch in (p or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def entity_matches_transaction(entity: dict, txn: dict) -> bool:
    vpa = txn.get("counterparty_vpa")
    if vpa and vpa in (entity.get("vpas") or []):
        return True
    name = txn.get("counterparty_name")
    if name and name in (entity.get("names") or []):
        return True
    mob = _norm_phone(txn.get("counterparty_mobile"))
    if mob and mob in {_norm_phone(p) for p in (entity.get("phones") or [])}:
        return True
    return False


def entity_matches_message(entity: dict, msg: dict) -> bool:
    sb = msg.get("sender_id")
    if sb and sb in (entity.get("sendbird_ids") or []):
        return True
    chat_with = msg.get("chat_with")
    if chat_with and chat_with in (entity.get("names") or []):
        return True
    return False


def related_records(entity: dict, transactions: list[dict],
                    messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """(matching transactions, matching messages) for one entity."""
    txns = [t for t in transactions if entity_matches_transaction(entity, t)]
    msgs = [m for m in messages if entity_matches_message(entity, m)]
    return txns, msgs


def entity_header_rows(entity: dict) -> list[tuple[str, str]]:
    """Key/value pairs for the pivot header card (skips empties)."""
    def join(key):
        return ", ".join(entity.get(key) or [])
    rows = [
        ("Names", join("names")),
        ("Phones", join("phones")),
        ("VPAs", join("vpas")),
        ("Customer IDs", join("customer_ids")),
        ("Sendbird IDs", join("sendbird_ids")),
        ("Type", entity.get("person_type") or ""),
        ("On Paytm", entity.get("account_age_text") or ""),
        ("Transactions", str(entity.get("txn_count") or 0)),
        ("Total received", f"₹{entity.get('total_received', 0):g}"),
        ("Total paid", f"₹{entity.get('total_paid', 0):g}"),
        ("Messages", str(entity.get("msg_count") or 0)),
    ]
    return [(k, v) for k, v in rows if v not in ("", "0", "₹0")] or [("(empty)", "—")]
