"""FR-13 Cross-artifact correlation / entity resolution.

Merges Person records that refer to the same real-world party (joining on customer_id,
phone, VPA, sendbird_id) and aggregates each counterparty's transactions (count, total
in/out) and message count. Produces 'entity' domain records.
"""
from __future__ import annotations

from typing import Iterator

import re

from ..core.casedb import CaseDB
from ..core.models import Record, Provenance, Origin

# company suffixes that vary between sources for the same merchant
_SUFFIX = re.compile(
    r"\b(private limited|pvt\.? ?ltd\.?|pvt\.? limited|limited|ltd\.?|inc\.?|llp|"
    r"technologies|technology|india)\b", re.I)


def norm_name(name: str | None) -> str | None:
    """Normalise a display name for entity joining.

    'FoodCo' / 'Foodco', 'QUICKEATS' / 'Quickeats', and
    'Acme Foods Pvt Ltd' / 'Acme Foods Private Limited' are the same
    counterparty; exact-match joining split each of them into two entities.
    """
    if not name:
        return None
    s = _SUFFIX.sub(" ", str(name).lower())
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s or None


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
        for n in self.names:
            nn = norm_name(n)
            if nn:
                ks.add(("name", nn))
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

    return _union_find(ents)


def _absorb(dst: _Entity, src: _Entity) -> None:
    dst.names |= src.names; dst.phones |= src.phones; dst.vpas |= src.vpas
    dst.customer_ids |= src.customer_ids; dst.sendbird_ids |= src.sendbird_ids
    dst.sources |= src.sources
    dst.is_subject = dst.is_subject or src.is_subject
    dst.person_type = dst.person_type or src.person_type
    dst.account_age = dst.account_age or src.account_age
    dst.keys |= src.keys


def _union_find(ents: list[_Entity]) -> list[_Entity]:
    """True union-find over shared join keys.

    The previous implementation broke on the first match and never re-checked earlier
    entities, so a transitive chain A-phone-B-vpa-C merged or not depending purely on row
    order. This maps every key to a set id and unions properly, so the result is
    order-independent.
    """
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(ents)):
        parent[i] = i
    key_owner: dict[tuple, int] = {}
    for i, e in enumerate(ents):
        for k in e.keys:
            if k in key_owner:
                union(key_owner[k], i)
            else:
                key_owner[k] = i
    # a union can make two previously-separate keys co-resident; iterate to a fixed point
    changed = True
    while changed:
        changed = False
        owner: dict[tuple, int] = {}
        for i, e in enumerate(ents):
            r = find(i)
            for k in e.keys:
                if k in owner and find(owner[k]) != r:
                    union(owner[k], i)
                    changed = True
                owner.setdefault(k, i)

    groups: dict[int, _Entity] = {}
    for i, e in enumerate(ents):
        r = find(i)
        if r not in groups:
            groups[r] = e
        elif groups[r] is not e:
            _absorb(groups[r], e)
    return list(groups.values())


def _seed_from_transactions(case: CaseDB, ents: list[_Entity]) -> list[_Entity]:
    """Create entities for counterparties that appear ONLY in the transaction ledger.

    Entities were previously seeded solely from the `person` domain (chatDb TBL_USERS +
    cache_table), so a passbook-only counterparty got no entity at all and its money was
    absent from every counterparty total.
    """
    known_vpa = {v for e in ents for v in e.vpas}
    known_name = {norm_name(n) for e in ents for n in e.names}
    known_phone = {p for e in ents for p in e.phones}
    extra: dict[tuple, _Entity] = {}
    for d in case.iter_domain("transaction"):
        vpa = d.get("counterparty_vpa")
        name = d.get("counterparty_name")
        phone = d.get("counterparty_mobile")
        if (vpa and vpa in known_vpa) or (name and norm_name(name) in known_name) \
           or (phone and phone in known_phone):
            continue
        key = ("vpa", vpa) if vpa else (("name", norm_name(name)) if name
                                        else (("phone", phone) if phone else None))
        if key is None:
            continue
        e = extra.get(key)
        if e is None:
            e = _Entity()
            e.sources.add((d.get("provenance") or {}).get("source_file", ""))
            extra[key] = e
        if vpa:
            e.vpas.add(vpa)
        if name:
            e.names.add(name)
        if phone:
            e.phones.add(phone)
        e.keys = e.join_keys()
    return ents + list(extra.values())


def build(case: CaseDB) -> Iterator[Record]:
    ents = _merge_people(case)
    ents = _union_find(_seed_from_transactions(case, ents))

    # index by vpa / name / sendbird for transaction + message attribution
    by_vpa = {}
    by_name = {}
    by_sb = {}
    by_phone = {}
    for e in ents:
        for v in e.vpas:
            by_vpa[v] = e
        for n in e.names:
            nn = norm_name(n)
            if nn:
                by_name.setdefault(nn, e)
        for p in e.phones:
            by_phone.setdefault(p, e)
        for s in e.sendbird_ids:
            by_sb[s] = e

    for d in case.iter_domain("transaction"):
        amt = d.get("amount") or 0.0
        target = None
        if d.get("counterparty_vpa") and d["counterparty_vpa"] in by_vpa:
            target = by_vpa[d["counterparty_vpa"]]
        elif d.get("counterparty_name") and norm_name(d["counterparty_name"]) in by_name:
            target = by_name[norm_name(d["counterparty_name"])]
        elif d.get("counterparty_mobile") and d["counterparty_mobile"] in by_phone:
            # mobile is parsed from searchableStrings but was never used as a join key
            target = by_phone[d["counterparty_mobile"]]
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
