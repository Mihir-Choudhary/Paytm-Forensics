"""Case lifecycle orchestration.

Ties together integrity hashing, artifact discovery, parser dispatch, the case DB, and the
audit log. A Case is created against an extraction folder and an output directory; it never
writes to the extraction.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from .. import __version__
from . import integrity
from .audit import AuditLog
from .artifact import discover, by_logical
from .casedb import CaseDB


@dataclass
class CaseMeta:
    case_id: str
    examiner: str
    evidence_number: str
    notes: str
    extraction_root: str
    created_utc: str
    tool_version: str


class Case:
    def __init__(self, extraction_root: str, out_dir: str, *,
                 case_id: str = "UNSET", examiner: str = "UNSET",
                 evidence_number: str = "UNSET", notes: str = ""):
        self.root = os.path.abspath(extraction_root)
        self.out_dir = os.path.abspath(out_dir)
        os.makedirs(self.out_dir, exist_ok=True)
        self.meta = CaseMeta(
            case_id=case_id, examiner=examiner, evidence_number=evidence_number,
            notes=notes, extraction_root=self.root,
            created_utc=datetime.now(timezone.utc).isoformat(),
            tool_version=__version__,
        )
        self.audit = AuditLog(os.path.join(self.out_dir, "audit.log"))
        self.casedb = CaseDB(os.path.join(self.out_dir, "case.db"))
        self.manifest: dict | None = None
        self.hash_index: dict[str, str] = {}

    # --- integrity --------------------------------------------------------- #
    def ingest(self) -> dict:
        self.audit.log("ingest_start", {"root": self.root})
        self.manifest = integrity.build_manifest(self.root)
        integrity.save_manifest(self.manifest, os.path.join(self.out_dir, "manifest.json"))
        self.hash_index = {
            e["rel_path"]: e.get("sha256") for e in self.manifest["files"]
        }
        with open(os.path.join(self.out_dir, "case_meta.json"), "w", encoding="utf-8") as f:
            json.dump(asdict(self.meta), f, indent=2, ensure_ascii=False)
        self.audit.log("ingest_done", {"file_count": self.manifest["file_count"]})
        return self.manifest

    def verify(self) -> dict:
        if self.manifest is None:
            raise RuntimeError("ingest() must run before verify()")
        report = integrity.verify_against(self.manifest, self.root)
        self.audit.log("verify", report)
        return report

    # --- parsing ----------------------------------------------------------- #
    def parse_all(self) -> dict:
        from ..parsers import REGISTRY  # late import so registry is populated
        arts = discover(self.root)
        idx = by_logical(arts)
        self.audit.log("discover", {"artifact_count": len(arts)})
        results: dict[str, int] = {}
        for cls in REGISTRY:
            parser = cls(idx, self.hash_index, arts)
            if not parser.available():
                continue
            try:
                n = self.casedb.add_many(parser.parse())
                results[parser.name] = n
                self.audit.log("parse", {"parser": parser.name, "records": n})
            except Exception as e:  # NFR-3: never let one parser crash the run
                results[parser.name] = -1
                self.audit.log("parse_error", {"parser": parser.name, "error": repr(e)})
        return results

    def carve(self) -> int:
        """Recover deleted/residual records from free/slack space (FR-11)."""
        from ..carving import carve_runner
        arts = discover(self.root)
        idx = by_logical(arts)
        n = self.casedb.add_many(carve_runner.run(self.casedb, idx, self.hash_index))
        self.audit.log("carve", {"carved_records": n})
        return n

    def correlate(self) -> int:
        """Entity resolution across parsed records (FR-13). Run before timeline."""
        from ..correlate import entities
        n = self.casedb.add_many(entities.build(self.casedb))
        self.audit.log("correlate", {"entities": n})
        return n

    def build_timeline(self) -> int:
        from ..correlate import timeline
        n = self.casedb.add_many(timeline.build(self.casedb))
        self.audit.log("timeline", {"events": n})
        return n

    def summary(self) -> dict:
        return self.casedb.count_by_domain()

    def close(self) -> None:
        self.casedb.close()
        self.audit.log("close", {})
