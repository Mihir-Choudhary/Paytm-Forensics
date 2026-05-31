"""SQLite deleted-record carver (FR-11).

Recovers residual records from a SQLite database file that are NOT returned by a normal
query: freelist pages, intra-page freeblocks (deleted cells), and the unallocated gap
between the cell-pointer array and the cell-content area.

Approach (defensible / "undark"-style):
  1. Parse the file header (page size, freelist).
  2. For every page, identify *unallocated* byte regions (gap + freeblock chain) and add
     whole freelist pages.
  3. Within those bytes, attempt to reconstruct SQLite *records* (serial-type header +
     body) at each candidate offset, independent of cell framing.
  4. Keep only reconstructed records that contain a forensic identifier (VPA/phone/RRN/
     txn-id) to control false positives; label every hit origin=carved with a confidence.

This never writes to the source; it reads the file as bytes once.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterator

from . import patterns

# byte-level identifier patterns for slack/freeblock scanning (clobbered-header cells)
_VPA_B = re.compile(rb"[a-zA-Z0-9._-]{2,40}@[a-zA-Z]{3,15}")
_RRN_B = re.compile(rb"(?<!\d)\d{12}(?!\d)")
_PHONE_B = re.compile(rb"(?<!\d)[6-9]\d{9}(?!\d)")
_TXN_B = re.compile(rb"(?:PYTM|PTM|AXI|HDF|UPI|ICI|SBI)[A-Za-z0-9]{8,40}")


# --------------------------- varint / serial types ------------------------ #
def read_varint(buf: bytes, off: int) -> tuple[int, int]:
    """Read a SQLite varint at off. Return (value, bytes_consumed)."""
    result = 0
    for i in range(9):
        if off + i >= len(buf):
            return result, i
        b = buf[off + i]
        if i == 8:
            result = (result << 8) | b
            return result, 9
        result = (result << 7) | (b & 0x7F)
        if not (b & 0x80):
            return result, i + 1
    return result, 9


def _serial_size(t: int) -> int:
    if t == 0 or t == 8 or t == 9:
        return 0
    if t in (1, 2, 3, 4):
        return t
    if t == 5:
        return 6
    if t == 6 or t == 7:
        return 8
    if t >= 12:
        return (t - 12) // 2 if t % 2 == 0 else (t - 13) // 2
    return -1


def _decode_value(buf: bytes, off: int, t: int):
    n = _serial_size(t)
    if n < 0 or off + n > len(buf):
        return None, -1
    seg = buf[off:off + n]
    if t == 0:
        return None, 0
    if t == 8:
        return 0, 0
    if t == 9:
        return 1, 0
    if t in (1, 2, 3, 4, 5, 6):
        return int.from_bytes(seg, "big", signed=True), n
    if t == 7:
        import struct
        try:
            return struct.unpack(">d", seg)[0], n
        except Exception:
            return None, n
    if t >= 12 and t % 2 == 1:  # text
        try:
            return seg.decode("utf-8", errors="strict"), n
        except UnicodeDecodeError:
            return None, -1
    return seg, n  # blob


def reconstruct_record(buf: bytes, off: int, max_cols: int = 60):
    """Try to parse a SQLite record at `off`. Return (values, total_len) or (None, 0)."""
    hdr_len, hc = read_varint(buf, off)
    if hc == 0 or hdr_len < 1 or hdr_len > 2000 or off + hdr_len > len(buf):
        return None, 0
    serials = []
    p = off + hc
    end_hdr = off + hdr_len
    while p < end_hdr:
        t, c = read_varint(buf, p)
        if c == 0:
            return None, 0
        serials.append(t)
        p += c
        if len(serials) > max_cols:
            return None, 0
    if p != end_hdr or not serials:
        return None, 0
    # decode body
    values = []
    body = end_hdr
    for t in serials:
        v, n = _decode_value(buf, body, t)
        if n < 0:
            return None, 0
        values.append(v)
        body += n
    # require at least one non-null, and plausible text content
    non_null = [v for v in values if v is not None]
    if not non_null:
        return None, 0
    return values, body - off


@dataclass
class CarvedRecord:
    page: int
    offset: int
    region: str                 # freelist | freeblock | gap
    values: list
    ids: dict = field(default_factory=dict)
    confidence: float = 0.5


class SqliteCarver:
    def __init__(self, path: str):
        with open(path, "rb") as f:
            self.data = f.read()
        self.page_size = self._page_size()
        self.npages = len(self.data) // self.page_size if self.page_size else 0
        self.freelist_first = int.from_bytes(self.data[32:36], "big") if len(self.data) >= 36 else 0
        self.freelist_count = int.from_bytes(self.data[36:40], "big") if len(self.data) >= 40 else 0

    def _page_size(self) -> int:
        if len(self.data) < 18 or self.data[:16] != b"SQLite format 3\x00":
            return 0
        ps = int.from_bytes(self.data[16:18], "big")
        return 65536 if ps == 1 else ps

    # ---- region identification ------------------------------------------- #
    def _page_bytes(self, pageno: int) -> bytes:
        start = (pageno - 1) * self.page_size
        return self.data[start:start + self.page_size]

    def _unallocated_regions(self) -> Iterator[tuple[int, str, bytes]]:
        """Yield (page_no, region_kind, region_bytes) for carvable regions."""
        if not self.page_size:
            return
        # 1. freelist pages (whole page is free)
        free_pages = self._walk_freelist()
        for pno in free_pages:
            yield pno, "freelist", self._page_bytes(pno)
        # 2. per-page gap + freeblocks on table-leaf pages
        for pno in range(1, self.npages + 1):
            if pno in free_pages:
                continue
            page = self._page_bytes(pno)
            hdr_off = 100 if pno == 1 else 0
            if hdr_off >= len(page):
                continue
            ptype = page[hdr_off]
            if ptype not in (0x0D, 0x0A):   # only leaf pages carry records
                continue
            try:
                ncells = int.from_bytes(page[hdr_off + 3:hdr_off + 5], "big")
                content_start = int.from_bytes(page[hdr_off + 5:hdr_off + 7], "big") or self.page_size
                first_free = int.from_bytes(page[hdr_off + 1:hdr_off + 3], "big")
            except Exception:
                continue
            cell_ptr_arr_end = hdr_off + 8 + ncells * 2
            # the gap between pointer array and content area
            if 0 < cell_ptr_arr_end < content_start <= len(page):
                yield pno, "gap", page[cell_ptr_arr_end:content_start]
            # freeblock chain (deleted cells)
            fb = first_free
            guard = 0
            while 0 < fb < len(page) - 3 and guard < 256:
                nxt = int.from_bytes(page[fb:fb + 2], "big")
                size = int.from_bytes(page[fb + 2:fb + 4], "big")
                if size <= 0 or fb + size > len(page):
                    break
                yield pno, "freeblock", page[fb:fb + size]
                fb = nxt
                guard += 1

    def _walk_freelist(self) -> set[int]:
        pages: set[int] = set()
        trunk = self.freelist_first
        guard = 0
        while trunk and guard < self.npages + 5:
            page = self._page_bytes(trunk)
            if len(page) < 8:
                break
            nxt = int.from_bytes(page[0:4], "big")
            nleaf = int.from_bytes(page[4:8], "big")
            for i in range(min(nleaf, (self.page_size - 8) // 4)):
                lp = int.from_bytes(page[8 + i * 4:12 + i * 4], "big")
                if 1 <= lp <= self.npages:
                    pages.add(lp)
            trunk = nxt
            guard += 1
        return pages

    # ---- carving --------------------------------------------------------- #
    def carve(self) -> Iterator[CarvedRecord]:
        """Two-pass carve over UNALLOCATED regions only:
          (1) structured SQLite record reconstruction (intact-header cells), and
          (2) byte-pattern scan for identifiers in clobbered-header / partial cells.
        Both passes operate solely on free/slack/freelist bytes, never live cells,
        so live data is never re-reported as carved.
        """
        if not self.page_size:
            return
        seen_sig = set()
        for pno, kind, region in self._unallocated_regions():
            # pass 1: structured reconstruction
            i = 0
            limit = len(region)
            while i < limit:
                values, total = reconstruct_record(region, i)
                if values and total >= 2 and patterns.has_any_identifier(values):
                    ids = patterns.classify(values)
                    sig = ("rec", tuple(ids["vpas"]), tuple(ids["rrns"]),
                           tuple(ids["txn_ids"]), tuple(ids["phones"]))
                    if sig not in seen_sig:
                        seen_sig.add(sig)
                        conf = 0.7 if kind == "freeblock" else 0.6
                        yield CarvedRecord(pno, i, kind, values, ids, conf)
                    i += max(total, 1)
                else:
                    i += 1
            # pass 2: pattern scan (recovers clobbered-header cell content)
            for ids, off in self._scan_patterns(region):
                sig = ("pat", tuple(ids["vpas"]), tuple(ids["rrns"]),
                       tuple(ids["txn_ids"]), tuple(ids["phones"]))
                if sig in seen_sig:
                    continue
                # skip if already covered by a structured record on this page
                covered = any(s[0] == "rec" and (set(ids["vpas"]) & set(s[1])
                              or set(ids["rrns"]) & set(s[2])) for s in seen_sig)
                if covered:
                    continue
                seen_sig.add(sig)
                conf = 0.45 if kind == "gap" else 0.5   # lower: atomic identifier, not full row
                yield CarvedRecord(pno, off, kind, [], ids, conf)

    @staticmethod
    def _scan_patterns(region: bytes):
        """Yield ({ids}, offset) for each identifier found in raw unallocated bytes."""
        for rx, key in ((_VPA_B, "vpas"), (_RRN_B, "rrns"),
                        (_PHONE_B, "phones"), (_TXN_B, "txn_ids")):
            for m in rx.finditer(region):
                try:
                    val = m.group(0).decode("ascii")
                except UnicodeDecodeError:
                    continue
                ids = {"vpas": [], "rrns": [], "phones": [], "txn_ids": [], "texts": []}
                # validate with the strict pattern validators
                if key == "vpas" and patterns.is_vpa(val):
                    ids["vpas"] = [val]
                elif key == "rrns" and patterns.is_rrn(val):
                    ids["rrns"] = [val]
                elif key == "phones" and patterns.is_phone(val):
                    ids["phones"] = [val]
                elif key == "txn_ids" and patterns.is_txn_id(val):
                    ids["txn_ids"] = [val]
                else:
                    continue
                yield ids, m.start()
