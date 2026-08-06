"""FR-12 Encrypted-artifact catalogue.

Records the presence, size, owning module and the *reason* each TEE-bound / encrypted
store cannot be decrypted offline. Does NOT attempt decryption (legally + technically
correct: keys live in the Android hardware keystore). See crypto analysis of DataUPI.xml.
"""
from __future__ import annotations

import json
import os
import re
from typing import Iterator

from .base import BaseParser, register
from ..core.models import EncryptedArtifact, Record

# filename -> (owning module, reason, cipher, keystore alias)
KNOWN = {
    "Data.xml": ("Wallet secure store",
                 "AES-256-GCM data key wrapped by RSA private key held in AndroidKeyStore (TEE) — private key non-exportable",
                 "AES-256-GCM + RSA-2048-OAEP-SHA256", "NPCI-UPI/wallet"),
    "DataUPI.xml": ("NPCI UPI security component",
                    "AES-256-GCM data key wrapped by RSA private key held in AndroidKeyStore (TEE); store also HMAC-integrity-checked",
                    "AES-256-GCM + RSA-2048-OAEP-SHA256", "NPCI-UPI"),
    "DataERUPEE.xml": ("NPCI e-Rupee security component",
                       "Same TEE-bound RSA-wrapped AES-GCM scheme as DataUPI",
                       "AES-256-GCM + RSA-2048-OAEP-SHA256", "NPCI-ERUPEE"),
}


@register
class EncryptedCatalogueParser(BaseParser):
    name = "encrypted.catalogue"
    needs = tuple(KNOWN.keys())

    def __init__(self, artifacts, hashes, all_artifacts=None):
        super().__init__(artifacts, hashes, all_artifacts)
        # every shared_jsons cache, not a hardcoded pair of filenames
        self._jsons = [a for a in self.all_artifacts
                       if a.rel_path.startswith("shared_jsons/")
                       and a.rel_path.lower().endswith(".json")]

    def available(self) -> bool:
        return bool(self._jsons) or any(n in self.artifacts for n in self.needs)

    def parse(self) -> Iterator[Record]:
        # TEE-bound secure prefs
        for fname, (mod, reason, cipher, alias) in KNOWN.items():
            art = self.get(fname)
            if not art:
                continue
            size = _size(art.abs_path)
            # read a header so the artifact is genuinely examined, not merely stat()ed,
            # and record what was actually observed alongside the documented cipher
            head = ""
            try:
                with open(art.abs_path, "rb") as f:
                    head = f.read(512).decode("utf-8", "replace")
            except OSError:
                pass
            # Record the SHAPE only. Emitting key names would copy identifiers out of a
            # TEE-bound store into the case file; FR-12 asks for presence and reason, not
            # content. (tests/test_m2.py::test_J guards exactly this.)
            n_keys = len(re.findall(r'name="([^"]{1,60})"', head))
            yield EncryptedArtifact(
                provenance=self.prov(art, None),
                raw={"entry_count_in_header": n_keys,
                     "is_xml": head.lstrip().startswith("<"),
                     "note": "header read to confirm format; contents not extracted"},
                name=fname, size=size, owning_module=mod,
                reason=reason, cipher=cipher, keystore_alias=alias,
            )
        # Every encrypted shared_jsons cache, detected by CONTENT SHAPE. FR-12 asks for
        # these to be enumerated; a two-name allowlist catalogued 2 of 21 on real data.
        for art in sorted(self._jsons, key=lambda a: a.rel_path):
            if not _is_ciphertext(art.abs_path):
                continue
            yield EncryptedArtifact(
                provenance=self.prov(art, None),
                raw={}, name=os.path.basename(art.rel_path), size=_size(art.abs_path),
                owning_module="Vertical JSON cache",
                reason="Base64 ciphertext at rest; decryption key not present in extraction (no offline recovery)",
                cipher="unknown (app-managed)", keystore_alias=None,
            )


_B64 = re.compile(rb"^[A-Za-z0-9+/\s]{64,}={0,2}\s*$")


def _is_ciphertext(path: str) -> bool:
    """True if the file is base64 ciphertext rather than readable JSON."""
    try:
        with open(path, "rb") as f:
            raw = f.read(65536)
    except OSError:
        return False
    s = raw.lstrip()
    if not s:
        return False
    if s[:1] in (b"{", b"["):
        return False              # plaintext JSON - not an encrypted cache
    return bool(_B64.match(raw))


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None
