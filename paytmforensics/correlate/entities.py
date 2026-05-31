"""FR-13 Cross-artifact correlation / entity resolution.

Merges Person records that refer to the same real-world party (joining on customer_id,
phone, VPA, sendbird_id) and aggregates each counterparty's transactions (count, total
in/out) and message count. Produces 'entity' domain records.
"""
from __future__ import annotations

from typing import Iterator

from ..core.casedb import CaseDB
from ..core.models import Record, Provenance, Origin


class _Entity:
    __slots__ = ("keys", "names", "phones", "vpas", "customer_ids", "sendbird_ids",
                 "person_type", "is_subject", "account_age", "sources",
                 "txn_in", "txn_out", "txn_count", "msg_count")

    def __init__(self):
        self.keys = set()
        self.names = set()
        self.phones = set()
        self.vpas = set()
        self.customer_ids = set()
        self.sendbird_ids = set()
        self.person_type = None
        self.is_subject = False
        self.account_age = None
        self.sources = set()
        self.txn_in = 0.0
        self.txn_out = 0.0
        self.txn_count = 0
        self.msg_count = 0

    def join_keys(self):
        ks = set()
        for c in self.customer_ids:
            ks.add(("cid", c))
        for p in self.phones:
            ks.add(("phone", p))
        for v in self.vpas:
            ks.add(("vpa", v))
        for s in self.sendbird_ids:
            ks.add(("sb", s))
        return ks


def _merge_people(case: CaseDB) -> list[_Entity]:
    ents: list[_Entity] = []
    for d in case.iter_domain("person"):
        e = _Entity()
        if d.get("name"):
            e.names.add(d["name"])
        if d.get("phone"):
            e.phones.add(d["phone"])
        for v in (d.get("vpas") or []):
            if v:
                e.vpas.add(v)
        if d.get("customer_id"):
            e.customer_ids.add(d["customer_id"])
        if d.get("sendbird_id"):
            e.sendbird_ids.add(d["sendbird_id"])
        e.person_type = d.get("person_type") or e.person_type
        e.is_subject = e.is_subject or bool(d.get("is_subject"))
        e.account_age = d.get("account_age_text") or e.account_age
        e.sources.add(d.get("provenance", {}).get("source_file", ""))
        e.keys = e.join_keys()
        ents.append(e)

    # union-find style merge on shared keys
    merged: list[_Entity] = []
    for e in ents:
        hit = None
        for m in merged:
            if e.keys & m.keys:
                hit = m
                break
        if hit:
            hit.names |= e.names; hit.phones |= e.phones; hit.vpas |= e.vpas
            hit.customer_ids |= e.customer_ids; hit.sendbird_ids |= e.sendbird_ids
            hit.sources |= e.sources
            hit.is_subject = hit.is_subject or e.is_subject
            hit.person_type = hit.person_type or e.person_type
            hit.account_age = hit.account_age or e.account_age
            hit.keys |= e.keys
        else:
            merged.append(e)
    return merged


def build(case: CaseDB) -> Iterator[Record]:
    ents = _merge_people(case)

    # index by vpa / name / sendbird for transaction + message attribution
    by_vpa = {}
    by_name = {}
    by_sb = {}
    for e in ents:
        for v in e.vpas:
            by_vpa[v] = e
        for n in e.names:
            by_name.setdefault(n, e)
        for s in e.sendbird_ids:
            by_sb[s] = e

    for d in case.iter_domain("transaction"):
        amt = d.get("amount") or 0.0
        target = None
        if d.get("counterparty_vpa") and d["counterparty_vpa"] in by_vpa:
            target = by_vpa[d["counterparty_vpa"]]
        elif d.get("counterparty_name") and d["counterparty_name"] in by_name:
            target = by_name[d["counterparty_name"]]
        if target is not None:
            target.txn_count += 1
            if d.get("settled"):                      # only completed money movements
                if d.get("direction") == "credit":
                    target.txn_in += amt
                elif d.get("direction") == "debit":
                    target.txn_out += amt

    for d in case.iter_domain("message"):
        sb = d.get("sender_id")
        if sb and sb in by_sb:
            by_sb[sb].msg_count += 1

    for e in ents:
        rec = Record(
            provenance=Provenance(source_file="(correlation)", origin=Origin.LIVE,
                                  confidence=1.0),
            raw={},
        )
        rec.domain = "entity"
        d = rec.to_dict()
        d.update({
            "names": sorted(e.names),
            "phones": sorted(e.phones),
            "vpas": sorted(e.vpas),
            "customer_ids": sorted(e.customer_ids),
            "sendbird_ids": sorted(e.sendbird_ids),
            "person_type": e.person_type,
            "is_subject": e.is_subject,
            "account_age_text": e.account_age,
            "txn_count": e.txn_count,
            "total_received": round(e.txn_in, 2),
            "total_paid": round(e.txn_out, 2),
            "msg_count": e.msg_count,
            "source_files": sorted(x for x in e.sources if x),
        })
        yield _DictRecord(d, rec.provenance)


class _DictRecord(Record):
    """Lightweight Record whose to_dict returns a pre-built dict (for entity rows)."""
    def __init__(self, d, prov):
        super().__init__(provenance=prov, raw={})
        self._d = d
        self.domain = "entity"

    def to_dict(self):
        return self._d
