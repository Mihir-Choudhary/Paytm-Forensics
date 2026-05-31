"""Make opaque config keys human-readable for analysts.

Produces (kind, meaning) for a config key/value:
  - `kind`   : a RELIABLE classification from the value/key shape (flag, URL, limit…)
  - `meaning`: a curated explanation for well-known keys, else a best-effort humanised
               version of the key name (clearly derived — not authoritative).
"""
from __future__ import annotations

import re

# expand well-known abbreviations found in Paytm config keys
_ABBR = {
    "kyc": "KYC", "otp": "OTP", "upi": "UPI", "vpa": "UPI-ID", "ppb": "Paytm Payments Bank",
    "pg": "payment gateway", "mte": "money transfer", "pps": "payments",
    "p2p": "peer-to-peer", "coft": "card-on-file tokenisation", "aotp": "auto-OTP",
    "vkyc": "video KYC", "gv": "gift voucher", "cc": "credit card", "emi": "EMI",
    "ios": "iOS", "android": "Android", "url": "URL", "sdk": "SDK", "api": "API",
    "ui": "UI", "amnt": "amount", "amt": "amount", "msg": "message", "pwd": "password",
    "min": "minimum", "max": "maximum", "txn": "transaction", "qr": "QR",
    "h5": "web (H5)", "tnc": "terms & conditions", "ttl": "cache lifetime",
}

# precise, hand-written explanations for high-value keys
_KNOWN = {
    "AID": "Application install ID (unique to this app installation)",
    "AcceptPayment_isForceUpdateAvailable": "Whether a forced app update is required to accept payments",
    "AddMoneyRefundOption": "Allowed refund destinations for the Add-Money flow",
    "AadhaarOtpFetchConsentURL": "Server URL shown for Aadhaar-OTP consent during KYC",
    "AAMThresholdAmntLowerLimit_iOS": "Add-Money lower amount threshold (iOS)",
    "GoldP2PMaxAmount": "Maximum amount allowed for a Gold peer-to-peer transfer",
    "MaxInstrumentCacheLimitAndroid": "Max number of payment instruments cached locally (Android)",
    "AutoReadOtpEnable": "Whether the app auto-reads incoming OTP SMS",
    "hawkeyeEngineRule": "Rules controlling which telemetry events the app logs/uploads",
    "walletLandingGenericURL_android": "Remote URL for the wallet landing screen layout (Android)",
    "vkycinternalH5Url": "Video-KYC web flow URL",
    "kyc_selfie_upload_url": "Server endpoint for KYC selfie/document upload",
}


def _is_num(v: str) -> bool:
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def classify(key: str, value) -> str:
    v = ("" if value is None else str(value)).strip()
    lk = (key or "").lower()
    if v.startswith("http://") or v.startswith("https://"):
        return "API endpoint / URL"
    if v.lower() in ("true", "false"):
        return "Feature flag (on/off)"
    if "rollout" in lk or "percentage" in lk:
        return "Experiment / rollout %"
    if v[:1] in ("{", "["):
        return "Structured config (JSON)"
    if _is_num(v) and any(t in lk for t in
                          ("threshold", "limit", "max", "min", "amount", "count",
                           "timeout", "ttl", "interval", "size", "duration")):
        return "Threshold / limit"
    if _is_num(v):
        return "Numeric setting"
    return "Setting / value"


def humanize(key: str) -> str:
    if not key:
        return ""
    k = re.sub(r"_(ios|android)$", "", key, flags=re.I)       # drop platform suffix
    k = k.replace("_", " ")
    k = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", k)                # camelCase -> spaces
    k = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", k)
    words = [_ABBR.get(w.lower(), w) for w in k.split() if w]
    return " ".join(words).strip()


def explain(key: str, value) -> tuple[str, str]:
    """Return (kind, meaning)."""
    kind = classify(key, value)
    if key in _KNOWN:
        return kind, _KNOWN[key]
    return kind, "≈ " + humanize(key)        # '≈' marks a derived (non-authoritative) label
