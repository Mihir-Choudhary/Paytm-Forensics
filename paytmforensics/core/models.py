"""Normalized forensic data model.

Every record that the tool surfaces carries full provenance so that any datum in a
report can be traced back to its exact source (file + table/key + rowid/offset) and
labelled as recovered from a live row or carved from free/slack space.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class Origin(str, Enum):
    LIVE = "live"
    CARVED = "carved"


@dataclass
class Provenance:
    """Where a single record came from. Attached to every Record."""
    source_file: str                 # relative path inside the extraction
    source_table: Optional[str] = None
    rowid: Optional[int] = None
    byte_offset: Optional[int] = None
    origin: Origin = Origin.LIVE
    confidence: float = 1.0          # 1.0 for live rows; <1.0 for carved/uncertain
    ingest_sha256: Optional[str] = None  # hash of the source file at ingest
    read_mode: Optional[str] = None  # "immutable" | "wal_applied" - see ingest/sqlite_ro

    def to_dict(self) -> dict:
        d = asdict(self)
        d["origin"] = self.origin.value
        return d


@dataclass
class Record:
    """Base class for every parsed artifact."""
    provenance: Provenance
    raw: dict = field(default_factory=dict)   # original column values, untouched

    # subclasses set this so the case DB / exporter knows the domain
    domain: str = "record"

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("provenance", "raw")}
        d = _normalise(d)
        d["domain"] = self.domain
        d["provenance"] = self.provenance.to_dict()
        d["raw"] = _normalise(self.raw)
        return d


def _normalise(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _normalise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalise(v) for v in obj]
    if isinstance(obj, bytes):
        return obj.hex()
    return obj


# --------------------------------------------------------------------------- #
#  Domain records
# --------------------------------------------------------------------------- #
@dataclass
class TimestampValue:
    """A timestamp shown three ways for full transparency (EV-7)."""
    raw: Any
    epoch_type: Optional[str] = None   # "unix_ms", "unix_s", "webkit_us", ...
    utc_iso: Optional[str] = None

    def to_dict(self) -> dict:
        return {"raw": self.raw, "epoch_type": self.epoch_type, "utc_iso": self.utc_iso}


@dataclass
class Person(Record):
    domain: str = "person"
    customer_id: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    country_code: Optional[str] = None
    vpas: list = field(default_factory=list)
    person_type: Optional[str] = None        # CUSTOMER / MERCHANT
    is_subject: bool = False                  # the device owner
    account_age_text: Optional[str] = None    # "On Paytm Since Mar 2022"
    sendbird_id: Optional[str] = None
    bank_name: Optional[str] = None
    masked_account: Optional[str] = None
    verified_name: Optional[str] = None


@dataclass
class Account(Record):
    domain: str = "account"
    bank_name: Optional[str] = None
    ifsc_or_branch: Optional[str] = None      # e.g. NNNN_HDFC0NNNNNN
    account_type: Optional[str] = None        # savings / Other
    masked_number: Optional[str] = None
    instrument_type: Optional[int] = None


@dataclass
class Transaction(Record):
    domain: str = "transaction"
    txn_id: Optional[str] = None
    source_txn_id: Optional[str] = None
    amount: Optional[float] = None
    direction: Optional[str] = None           # credit / debit
    counterparty_vpa: Optional[str] = None
    counterparty_name: Optional[str] = None
    counterparty_mobile: Optional[str] = None
    rrn: Optional[str] = None                 # NPCI reference number
    account_used: Optional[str] = None        # subject's bank account/instrument id used
    account_type: Optional[str] = None        # savings / current / Other
    account_bank: Optional[str] = None        # resolved from IFSC bank code
    account_branch: Optional[str] = None      # IFSC branch code (raw)
    payment_mode: Optional[str] = None        # UPI / wallet
    status_raw: Optional[Any] = None
    status_label: Optional[str] = None
    category_raw: Optional[Any] = None
    category_label: Optional[str] = None
    tag: Optional[str] = None
    narration: Optional[str] = None
    instrument: Optional[str] = None          # bank account used
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    settled: Optional[bool] = None             # True only for completed money movements
    txn_source: Optional[str] = None           # "passbook" | "chat"
    note: Optional[str] = None
    timestamp: Optional[dict] = None          # TimestampValue.to_dict()


@dataclass
class Message(Record):
    domain: str = "message"
    channel_url: Optional[str] = None
    chat_with: Optional[str] = None           # resolved counterparty (conversation name)
    sender_id: Optional[str] = None
    sender_name: Optional[str] = None
    msg_type: Optional[str] = None            # TRANSFER / UPI_REQUEST / text
    content: Optional[str] = None
    amount: Optional[float] = None
    rrn: Optional[str] = None
    status: Optional[str] = None
    encrypted_blob_present: bool = False
    timestamp: Optional[dict] = None


@dataclass
class Channel(Record):
    """A conversation record from TBL_CHANNELS, independent of surviving messages."""
    domain: str = "channel"
    channel_url: Optional[str] = None
    name: Optional[str] = None
    member_count: Optional[int] = None
    message_count: int = 0
    has_messages: bool = False
    timestamp: Optional[dict] = None


@dataclass
class LocationFix(Record):
    domain: str = "location"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    speed: Optional[float] = None
    source_kind: Optional[str] = None         # signal / cookie
    pincode: Optional[str] = None
    timestamp: Optional[dict] = None


@dataclass
class Consent(Record):
    domain: str = "consent"
    consent_key: Optional[str] = None
    consent_value: Optional[str] = None
    synced_with_server: Optional[bool] = None
    timestamp: Optional[dict] = None


@dataclass
class Job(Record):
    domain: str = "job"
    worker_class: Optional[str] = None
    job_names: list = field(default_factory=list)   # WorkName rows (human-readable)
    tags: list = field(default_factory=list)        # WorkTag rows
    state: Optional[Any] = None
    state_label: Optional[str] = None
    last_enqueue: Optional[dict] = None
    interval_ms: Optional[int] = None
    run_attempt_count: Optional[int] = None


@dataclass
class Notification(Record):
    domain: str = "notification"
    title: Optional[str] = None
    message: Optional[str] = None
    deep_link: Optional[str] = None
    campaign_id: Optional[str] = None
    push_id: Optional[str] = None
    expiry: Optional[dict] = None            # push-dedup expiry (NOT an event time)
    is_dedup_record: bool = False            # structural flag; do not match on display text
    timestamp: Optional[dict] = None


@dataclass
class SearchQuery(Record):
    domain: str = "search"
    query: Optional[str] = None
    vertical_id: Optional[str] = None
    url: Optional[str] = None
    timestamp: Optional[dict] = None


@dataclass
class ConfigItem(Record):
    domain: str = "config"
    key: Optional[str] = None
    kind: Optional[str] = None                 # Feature flag / URL / Threshold / ...
    meaning: Optional[str] = None              # human-readable explanation of the key
    value: Optional[str] = None
    is_changed_from_default: Optional[bool] = None


@dataclass
class DiagnosticEvent(Record):
    domain: str = "diagnostic"
    event_type: Optional[str] = None
    customer_id: Optional[str] = None
    device_id: Optional[str] = None
    app_version: Optional[str] = None
    network_type: Optional[str] = None
    message: Optional[str] = None             # customMessage — what the event is about
    flow_name: Optional[str] = None
    screen_name: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    battery_pct: Optional[int] = None
    network_carrier: Optional[str] = None
    process_state: Optional[str] = None
    is_rooted: Optional[bool] = None
    session_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timestamp: Optional[dict] = None


@dataclass
class EncryptedArtifact(Record):
    domain: str = "encrypted"
    name: Optional[str] = None
    size: Optional[int] = None
    owning_module: Optional[str] = None
    reason: Optional[str] = None              # why it can't be decrypted
    cipher: Optional[str] = None
    keystore_alias: Optional[str] = None


@dataclass
class AppStateItem(Record):
    domain: str = "appstate"
    key: Optional[str] = None
    value: Optional[str] = None
    detail: Optional[str] = None
    timestamp: Optional[dict] = None


@dataclass
class CrashSession(Record):
    domain: str = "crash"
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    start_time: Optional[dict] = None
    preview: Optional[str] = None


@dataclass
class PrefItem(Record):
    domain: str = "pref"
    key: Optional[str] = None
    value: Optional[str] = None
    pref_file: Optional[str] = None


@dataclass
class Capability(Record):
    """A feature the account *can* use (from passbook filter config / remote-config flags).
    Documents availability — NOT that the feature was used."""
    domain: str = "capability"
    category: Optional[str] = None     # Payment instrument / Automatic payments / etc.
    name: Optional[str] = None
    value: Optional[str] = None
    available: Optional[bool] = None


@dataclass
class TimelineEvent(Record):
    domain: str = "timeline"
    utc_iso: Optional[str] = None
    event_type: Optional[str] = None
    summary: Optional[str] = None
    ref_domain: Optional[str] = None


@dataclass
class Cookie(Record):
    domain: str = "cookie"
    host: Optional[str] = None
    name: Optional[str] = None
    value: Optional[str] = None
    is_secure: Optional[bool] = None
    is_httponly: Optional[bool] = None
    created: Optional[dict] = None
    expires: Optional[dict] = None


@dataclass
class WebCacheEntry(Record):
    domain: str = "webcache"
    url: Optional[str] = None
    method: Optional[str] = None
    user_id: Optional[str] = None
    size: Optional[int] = None
    body_preview: Optional[str] = None
    created: Optional[dict] = None


@dataclass
class WebStorageItem(Record):
    domain: str = "webstorage"
    origin: Optional[str] = None
    key: Optional[str] = None
    value: Optional[str] = None
    store_kind: Optional[str] = None       # local_storage / session_storage
    best_effort: bool = True
