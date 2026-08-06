"""Append-only audit log (EV-5).

Every meaningful action (open, parse, carve, filter, export) is appended with a UTC
timestamp. The log is newline-delimited JSON so it is human-readable and tamper-evident
(each line also carries a running hash chain).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone


class AuditLog:
    def __init__(self, path: str):
        self.path = path
        self._prev_hash = "0" * 64
        # if a log exists, recover the last hash to continue the chain
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            self._prev_hash = json.loads(line).get("entry_hash", self._prev_hash)
            except Exception:
                pass

    def log(self, action: str, detail: dict | None = None) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "detail": detail or {},
            "prev_hash": self._prev_hash,
        }
        payload = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        entry_hash = hashlib.sha256((self._prev_hash + payload).encode("utf-8")).hexdigest()
        entry["entry_hash"] = entry_hash
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._prev_hash = entry_hash

    # ------------------------------------------------------------------ #
    @staticmethod
    def verify(path: str) -> dict:
        """Verify a log's hash chain end to end (EV-5).

        The log was tamper-EVIDENT but not tamper-CHECKABLE: nothing in the package could
        validate it, so a third party had to write their own verifier. Returns
        ``{"ok", "entries", "breaks": [{"line", "reason"}]}``.
        """
        breaks: list[dict] = []
        prev = "0" * 64
        n = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    n += 1
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        breaks.append({"line": i, "reason": "not valid JSON"})
                        continue
                    if entry.get("prev_hash") != prev:
                        breaks.append({"line": i, "reason": "prev_hash does not chain"})
                    body = {k: v for k, v in entry.items() if k != "entry_hash"}
                    payload = json.dumps(body, sort_keys=True, ensure_ascii=False)
                    expect = hashlib.sha256(
                        (body.get("prev_hash", "") + payload).encode("utf-8")).hexdigest()
                    if expect != entry.get("entry_hash"):
                        breaks.append({"line": i, "reason": "entry_hash mismatch (edited?)"})
                    prev = entry.get("entry_hash", prev)
        except OSError as e:
            return {"ok": False, "entries": 0, "breaks": [{"line": 0, "reason": str(e)}]}
        return {"ok": not breaks, "entries": n, "breaks": breaks}
