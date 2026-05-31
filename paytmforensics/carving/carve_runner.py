"""Runs the SQLite carver across the evidence DBs and emits carved Records.

Carved records are deduped against the live values already in the case DB, so the output
is strictly the *additional* (deleted/residual) identifiers recovered from free/slack
space. Everything is labelled origin=carved with a confidence < 1.0 (EV-8).
"""
from __future__ import annotations

from typing import Iterator

from .sqlite_carver import SqliteCarver
from ..core.artifact import Artifact
from ..core.casedb import CaseDB
from ..core.models import Record, Provenance, Origin

# which evidence DBs are worth carving for financial/comms identifiers
CARVE_TARGETS = ("chatDb.db", "passbook.db", "cache_database", "contacts")


def _live_values(case: CaseDB) -> dict[str, set]:
    """Collect identifiers already recovered live, to subtract from carved output."""
    live = {"vpas": set(), "rrns": set(), "txn_ids": set(), "phones": set()}
    for d in case.iter_domain("transaction"):
        if d.get("counterparty_vpa"):
            live["vpas"].add(d["counterparty_vpa"])
        if d.get("rrn"):
            live["rrns"].add(d["rrn"])
        if d.get("source_txn_id"):
            live["txn_ids"].add(d["source_txn_id"])
    for d in case.iter_domain("person"):
        if d.get("phone"):
            live["phones"].add(d["phone"])
        for v in (d.get("vpas") or []):
            live["vpas"].add(v)
    return live


class _CarvedRecord(Record):
    def __init__(self, d, prov):
        super().__init__(provenance=prov, raw={})
        self._d = d
        self.domain = "carved"

    def to_dict(self):
        return self._d


def run(case: CaseDB, artifacts: dict[str, Artifact], hashes: dict[str, str]) -> Iterator[Record]:
    live = _live_values(case)
    for logical in CARVE_TARGETS:
        art = artifacts.get(logical)
        if not art:
            continue
        try:
            carver = SqliteCarver(art.abs_path)
        except OSError:
            continue
        for cr in carver.carve():
            ids = cr.ids
            # keep only NEW identifiers not already present in live data
            new_vpas = [v for v in ids["vpas"] if v not in live["vpas"]]
            new_rrns = [r for r in ids["rrns"] if r not in live["rrns"]]
            new_txns = [t for t in ids["txn_ids"] if t not in live["txn_ids"]]
            new_phones = [p for p in ids["phones"] if p not in live["phones"]]
            if not (new_vpas or new_rrns or new_txns or new_phones):
                continue
            prov = Provenance(
                source_file=art.rel_path,
                source_table=None,
                byte_offset=(cr.page - 1) * carver.page_size + cr.offset,
                origin=Origin.CARVED,
                confidence=cr.confidence,
                ingest_sha256=hashes.get(art.rel_path),
            )
            rec = _CarvedRecord({}, prov)
            d = Record(provenance=prov, raw={}).to_dict()
            d["domain"] = "carved"
            d.update({
                "region": cr.region,
                "page": cr.page,
                "vpas": new_vpas,
                "rrns": new_rrns,
                "txn_ids": new_txns,
                "phones": new_phones,
                "texts": ids["texts"][:5],
                "confidence": cr.confidence,
            })
            rec._d = d
            yield rec
