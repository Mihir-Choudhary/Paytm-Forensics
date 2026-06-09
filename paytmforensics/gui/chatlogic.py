"""Pure-Python chat presentation logic (no Qt) — unit-testable headlessly.

Keeps the heuristics that decide a payment bubble's status and a conversation's
list label out of the widget code, so they can be regression-tested without a
display and tuned against real data.
"""
from __future__ import annotations


def payment_status(msg_type: str | None, content: str | None,
                   amount: float | None) -> str:
    """Classify a chat message's payment outcome.

    Returns one of "", "failed", "declined", "requested", "success".
    Only messages in a payment context are classified — a plain text message
    that merely contains the word "fail" must NOT get a red failure badge.
    msg_type is authoritative; message text is only a fallback.
    """
    mt = (msg_type or "").upper()
    payment_ctx = bool(amount) or any(
        k in mt for k in ("TRANSFER", "REQUEST", "UPI", "PAY"))
    if not payment_ctx:
        return ""
    c = (content or "").lower()
    if "FAIL" in mt:
        return "failed"
    if "DECLINE" in mt:
        return "declined"
    if "REQUEST" in mt and "RESPONSE" not in mt:
        return "requested"
    if "RESPONSE" in mt:
        # a response message carries its outcome in the rendered text —
        # a declined request is still a *_RESPONSE
        if "declin" in c:
            return "declined"
        if "fail" in c:
            return "failed"
        return "success"
    if "TRANSFER" in mt:
        # an executed transfer is success by type; free-text notes must not
        # be able to fake a failure badge
        return "success"
    if "fail" in c:
        return "failed"
    if "declin" in c:
        return "declined"
    if "approved" in c:
        return "success"
    return ""


def conversation_label(items: list[dict], subject_sb: str | None,
                       channel_url: str | None) -> str:
    """Human label for a conversation list entry.

    Preference order: resolved counterparty (chat_with) → first non-subject
    sender name → channel-suffix fallback, so several unnamed merchant
    channels stay distinguishable instead of all reading "Outgoing (merchant)".
    """
    name = next((m.get("chat_with") for m in items if m.get("chat_with")), None)
    if not name:
        name = next((m.get("sender_name") for m in items
                     if m.get("sender_id") != subject_sb and m.get("sender_name")),
                    None)
    if not name:
        tail = (channel_url or "")[-6:]
        name = f"Merchant / outgoing …{tail}" if tail else "Outgoing (merchant)"
    return name
