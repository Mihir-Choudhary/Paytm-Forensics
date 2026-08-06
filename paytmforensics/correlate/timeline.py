"""FR-14 Master timeline: merge every timestamped record into one chronological view."""
from __future__ import annotations

from typing import Iterator

from ..core.casedb import CaseDB
from ..core.models import TimelineEvent, Provenance, Origin


def _utc(ts: dict | None) -> str | None:
    return ts.get("utc_iso") if isinstance(ts, dict) else None


def build(case: CaseDB) -> Iterator[TimelineEvent]:
    """Read parsed records back out of the case DB and emit timeline events."""
    builders = {
        "transaction": lambda d: (
            _utc(d.get("timestamp")),
            "transaction",
            f"{d.get('direction') or ''} {d.get('amount') or ''} "
            f"{d.get('counterparty_name') or d.get('counterparty_vpa') or ''}".strip(),
        ),
        "message": lambda d: (
            _utc(d.get("timestamp")), "message",
            f"{d.get('msg_type') or 'msg'}: {d.get('content') or ''}".strip(),
        ),
        "location": lambda d: (
            _utc(d.get("timestamp")), "location",
            f"{d.get('latitude')},{d.get('longitude')} ({d.get('source_kind')})",
        ),
        "consent": lambda d: (
            _utc(d.get("timestamp")), "consent",
            f"{d.get('consent_key')} = {d.get('consent_value')}",
        ),
        "notification": lambda d: (
            _utc(d.get("timestamp")), "notification", d.get("title") or d.get("message") or "",
        ),
        "search": lambda d: (
            _utc(d.get("timestamp")), "search", d.get("query") or "",
        ),
        "diagnostic": lambda d: (
            _utc(d.get("timestamp")), "diagnostic",
            f"{d.get('event_type')} v{d.get('app_version')} {d.get('network_type') or ''}".strip(),
        ),
        # FR-14 lists jobs explicitly; crash/appstate/cookie/webcache are timestamped too
        "job": lambda d: (
            _utc(d.get("last_enqueue")), "job",
            f"{(d.get('worker_class') or '').rsplit('.', 1)[-1]} [{d.get('state_label') or ''}]".strip(),
        ),
        "crash": lambda d: (
            _utc(d.get("start_time")), "crash",
            f"session {d.get('session_id') or ''}".strip(),
        ),
        "appstate": lambda d: (
            _utc(d.get("timestamp")), "appstate",
            f"{d.get('key') or ''}: {d.get('value') or ''}".strip(),
        ),
        "cookie": lambda d: (
            _utc(d.get("created")), "cookie",
            f"{d.get('host') or ''} {d.get('name') or ''}".strip(),
        ),
        "channel": lambda d: (
            _utc(d.get("timestamp")), "channel",
            f"conversation {d.get('name') or d.get('channel_url') or ''} "
            f"({d.get('message_count', 0)} msg)".strip(),
        ),
        "webcache": lambda d: (
            _utc(d.get("created")), "webcache", (d.get("url") or "")[:120],
        ),
    }
    events: list[TimelineEvent] = []
    for domain, fn in builders.items():
        for d in case.iter_domain(domain):
            # payment messages are represented as transactions; skip them here to avoid
            # duplicate timeline entries for the same payment.
            if domain == "message" and d.get("amount"):
                continue
            # push-dedup bookkeeping has no real event time (only an expiry) — exclude it.
            # Keyed on a structural flag, not on the display string it used to match.
            if domain == "notification" and (d.get("is_dedup_record")
                                             or d.get("message") == "(push dedup record)"):
                continue
            utc, etype, summary = fn(d)
            if not utc:
                continue
            prov_d = d.get("provenance", {})
            events.append(TimelineEvent(
                provenance=Provenance(
                    source_file=prov_d.get("source_file", ""),
                    source_table=prov_d.get("source_table"),
                    rowid=prov_d.get("rowid"),
                    origin=Origin(prov_d.get("origin", "live")),
                    confidence=prov_d.get("confidence", 1.0),
                    ingest_sha256=prov_d.get("ingest_sha256"),   # carry source hash
                    read_mode=prov_d.get("read_mode"),
                ),
                utc_iso=utc,
                event_type=etype,
                summary=summary,
                ref_domain=domain,
            ))
    # A "master chronological timeline" must BE chronological wherever it is consumed --
    # report.html and the CSV/JSON exports render stored order, and only the GUI sorted.
    # Stable tiebreak keeps exports byte-identical across runs (EV-6).
    events.sort(key=lambda e: (e.utc_iso, e.ref_domain, e.summary or ""))
    yield from events
