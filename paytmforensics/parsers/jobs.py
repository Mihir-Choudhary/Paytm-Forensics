"""FR-7 Device behaviour: WorkManager background jobs (androidx.work.workdb)."""
from __future__ import annotations

from typing import Iterator

from .base import BaseParser, register
from ..core.models import Job, Record
from ..ingest import sqlite_ro as sql
from ..enrich import enums, timestamps


@register
class WorkManagerParser(BaseParser):
    name = "jobs"
    needs = ("androidx.work.workdb",)

    def parse(self) -> Iterator[Record]:
        art = self.get("androidx.work.workdb")
        if not art:
            return
        with self.open(art) as con:
            tables = sql.list_tables(con)
            if "WorkSpec" not in tables:
                return
            # human-readable names/tags, so the grid is not just raw worker classes
            names: dict = {}
            if "WorkName" in tables:
                for _rid, r in sql.rows(con, "WorkName"):
                    wid = r.get("work_spec_id")
                    if wid and r.get("name"):
                        names.setdefault(wid, []).append(r["name"])
            tags: dict = {}
            if "WorkTag" in tables:
                for _rid, r in sql.rows(con, "WorkTag"):
                    wid = r.get("work_spec_id")
                    if wid and r.get("tag"):
                        tags.setdefault(wid, []).append(r["tag"])
            for rowid, r in sql.rows(con, "WorkSpec"):
                wid = r.get("id")
                yield Job(
                    provenance=self.prov(art, "WorkSpec", rowid),
                    raw={k: r.get(k) for k in ("id", "state", "worker_class_name",
                                                "last_enqueue_time", "interval_duration",
                                                "run_attempt_count", "period_count")},
                    worker_class=r.get("worker_class_name"),
                    job_names=names.get(wid, []),
                    tags=tags.get(wid, []),
                    state=r.get("state"),
                    state_label=enums.work_state(r.get("state")),
                    last_enqueue=timestamps.decode(r.get("last_enqueue_time")).to_dict(),
                    interval_ms=_int(r.get("interval_duration")),
                    run_attempt_count=_int(r.get("run_attempt_count")),
                )


def _int(v):
    try:
        return int(v)
    except (ValueError, TypeError):
        return None
