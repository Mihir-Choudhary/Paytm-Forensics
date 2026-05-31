"""FR-12 Encrypted-artifact catalogue.

Records the presence, size, owning module and the *reason* each TEE-bound / encrypted
store cannot be decrypted offline. Does NOT attempt decryption (legally + technically
correct: keys live in the Android hardware keystore). See crypto analysis of DataUPI.xml.
"""
from __future__ import annotations

import os
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
    needs = tuple(KNOWN.keys()) + ("TPAP_UPI.json", "SMS_smssdk_pref.json")

    def parse(self) -> Iterator[Record]:
        # TEE-bound secure prefs
        for fname, (mod, reason, cipher, alias) in KNOWN.items():
            art = self.get(fname)
            if not art:
                continue
            size = _size(art.abs_path)
            yield EncryptedArtifact(
                provenance=self.prov(art, None),
                raw={}, name=fname, size=size, owning_module=mod,
                reason=reason, cipher=cipher, keystore_alias=alias,
            )
        # encrypted shared_jsons caches (base64 ciphertext; key likely keystore-backed)
        for fname in ("TPAP_UPI.json", "SMS_smssdk_pref.json"):
            art = self.get(fname)
            if not art:
                continue
            yield EncryptedArtifact(
                provenance=self.prov(art, None),
                raw={}, name=fname, size=_size(art.abs_path),
                owning_module="Vertical JSON cache",
                reason="Base64 ciphertext at rest; decryption key not present in extraction (no offline recovery)",
                cipher="unknown (app-managed)", keystore_alias=None,
            )


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None
