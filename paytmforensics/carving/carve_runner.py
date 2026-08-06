"""Runs the SQLite carver across the evidence DBs and emits carved Records.

Carved records are deduped against the live values already in the case DB, so the output
is strictly the *additional* (deleted/residual) identifiers recovered from free/slack
space. Everything is labelled origin=carved with a confidence < 1.0 (EV-8).
"""
from __future__ import annotations

import os
from typing import Iterator

from .sqlite_carver import SqliteCarver, WalCarver
from ..core.artifact import Artifact
from ..core.casedb import CaseDB
from ..core.models import Record, Provenance, Origin

# Carve EVERY SQLite database in the extraction, plus its -wal. Restricting this to a
# handful of names made recovery a scope decision rather than a source-state one, and the
# -wal is the richest source of recently-deleted rows on a live-extracted device.
CARVE_TARGETS = ()   # empty => every discovered SQLite artifact (see _targets)


def _targets(artifacts):
    """Every artifact that is actually a SQLite database, by magic bytes."""
    out = []
    seen = set()
    for a in artifacts.values():
        if a.abs_path in seen:
            continue
        try:
            with open(a.abs_path, "rb") as f:
                if f.read(16) != b"SQLite format 3\x00":
                    continue
        except OSError:
            continue
        seen.add(a.abs_path)
        out.append(a)
    return sorted(out, key=lambda a: a.rel_path)


def _live_values(case: CaseDB) -> dict[str, set]:
    """Collect identifiers already recovered live, to subtract from carved output.

    Covers EVERY domain that can carry an identifier -- notably `message`, whose RRNs were
    previously omitted, so a live chat RRN found in freed space (SQLite frees the old cell
    on every UPDATE, and message rows are updated constantly) was reported as a newly
    recovered deleted record.
    """
    live = {"vpas": set(), "rrns": set(), "txn_ids": set(), "phones": set()}

    def add(d):
        for key, field in (("vpas", "counterparty_vpa"), ("rrns", "rrn"),
                           ("txn_ids", "source_txn_id"), ("txn_ids", "txn_id"),
                           ("phones", "counterparty_mobile"), ("phones", "phone")):
            v = d.get(field)
            if v:
                live[key].add(v)
        for v in (d.get("vpas") or []):
            live["vpas"].add(v)
        for v in (d.get("phones") or []):
            live["phones"].add(v)

    for dom in ("transaction", "message", "person", "entity"):
        for d in case.iter_domain(dom):
            add(d)
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
    for art in _targets(artifacts):
        carvers = []
        try:
            carvers.append((art, SqliteCarver(art.abs_path)))
        except OSError:
            pass
        # the -wal holds old page images: prime material for recently-deleted rows
        wal = art.abs_path + "-wal"
        if os.path.exists(wal) and os.path.getsize(wal) > 0:
            try:
                carvers.append((art, WalCarver(wal)))
            except OSError:
                pass
        for src_art, carver in carvers:
            yield from _emit(carver, src_art, live, hashes)


def _emit(carver, art, live, hashes):
    """Yield carved records from one carver, minus identifiers already known live."""
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
