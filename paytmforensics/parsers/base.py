"""Parser base class and registry.

Each parser declares which logical artifact(s) it needs and yields Record objects.
Parsers read by column name and tolerate missing columns/tables (schema-drift tolerance).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from ..core.artifact import Artifact
from ..core.models import Provenance, Origin, Record

REGISTRY: list[type["BaseParser"]] = []


def register(cls):
    REGISTRY.append(cls)
    return cls


class BaseParser:
    #: logical artifact names this parser consumes (filenames / known names)
    needs: tuple[str, ...] = ()
    #: human label
    name: str = "base"

    def __init__(self, artifacts: dict[str, Artifact], hashes: dict[str, str],
                 all_artifacts: list[Artifact] | None = None):
        self.artifacts = artifacts          # logical name -> Artifact (deduped by filename)
        self.hashes = hashes                # rel_path -> sha256
        # full list (no dedup) for parsers that scan many same-named files
        self.all_artifacts = all_artifacts if all_artifacts is not None else list(artifacts.values())
        #: how the most recent database was read; carried into Provenance.read_mode
        self._read_mode: str | None = None

    @contextmanager
    def open(self, art: "Artifact"):
        """Open an evidence DB the most complete way that is still read-only.

        Uses a verified scratch copy when a -wal is present so uncheckpointed rows are
        visible (and WAL-deleted rows are correctly absent); falls back to an immutable
        open otherwise. Records the mode for provenance.
        """
        from ..ingest import sqlite_ro as _sql
        with _sql.open_best(art.abs_path) as (con, mode):
            self._read_mode = mode
            yield con

    def available(self) -> bool:
        return any(n in self.artifacts for n in self.needs)

    def get(self, logical: str) -> Artifact | None:
        return self.artifacts.get(logical)

    def prov(self, art: Artifact, table: str | None = None,
             rowid: int | None = None, origin: Origin = Origin.LIVE,
             confidence: float = 1.0) -> Provenance:
        return Provenance(
            source_file=art.rel_path,
            source_table=table,
            rowid=rowid,
            origin=origin,
            confidence=confidence,
            ingest_sha256=self.hashes.get(art.rel_path),
            read_mode=self._read_mode,
        )

    def parse(self) -> Iterator[Record]:
        raise NotImplementedError
