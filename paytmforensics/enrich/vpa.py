"""UPI VPA helpers: parse the PSP (handle) and classify likely entity type."""
from __future__ import annotations

from typing import Optional

# Common PSP handles -> sponsor bank / provider (for examiner context, not exhaustive)
PSP_HANDLES = {
    "icici": "ICICI Bank", "ibl": "ICICI Bank", "okicici": "ICICI (Google Pay)",
    "ptybl": "Paytm Payments Bank", "paytm": "Paytm",
    "ptaxis": "Axis (Paytm)", "axisbank": "Axis Bank", "axl": "Axis Bank",
    "okaxis": "Axis (Google Pay)",
    "hdfcbank": "HDFC Bank", "okhdfcbank": "HDFC (Google Pay)", "payzapp": "HDFC PayZapp",
    "ybl": "Yes Bank (PhonePe)", "yesbank": "Yes Bank", "yesbankltd": "Yes Bank",
    "ypbiz": "Yes Bank (business)",
    "oksbi": "SBI (Google Pay)", "sbi": "SBI",
    "airtel": "Airtel Payments Bank", "mairtel": "Airtel", "rxairtel": "Airtel",
    "apl": "Amazon Pay", "fbpe": "BharatPe (Yes/ICICI)", "idfcbank": "IDFC First",
    "famc": "Trans Bank (Fam)", "ptsbi": "SBI (Paytm)",
}


def split(vpa: str) -> tuple[Optional[str], Optional[str]]:
    if not vpa or "@" not in vpa:
        return (vpa or None, None)
    user, _, handle = vpa.partition("@")
    return user, handle.lower()


def psp_name(vpa: str) -> Optional[str]:
    _user, handle = split(vpa)
    if not handle:
        return None
    return PSP_HANDLES.get(handle, handle)


def looks_like_merchant(vpa: str) -> bool:
    """Heuristic: merchant VPAs are typically long alphanumerics or contain brand tokens."""
    user, _ = split(vpa)
    if not user:
        return False
    u = user.lower()
    brandish = any(t in u for t in ("paytm-", "swiggy", "zomato", "redbus", "bharatpe",
                                    "amzn", "rzp", "payu", "easebuzz", "hungerbox"))
    return brandish or len(user) > 18
