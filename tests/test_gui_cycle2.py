"""Regression tests for the cycle-2 GUI batch (2026-06-10). Synthetic data only.

Covers:
  - parse_user_date validation/normalisation (shared by table + timeline filters)
  - chatlogic.payment_status: no failure badge on plain text containing "fail";
    msg_type authoritative over content
  - chatlogic.conversation_label: chat_with > sender > distinguishable fallback
  - timeline _to_events + FilterSpec date/text filtering drive the same rows
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from paytmforensics.gui.filters import FilterSpec, apply_filter, parse_user_date
from paytmforensics.gui.chatlogic import payment_status, conversation_label

try:                                    # timelineview needs Qt; logic tests don't
    from PySide6 import QtWidgets       # noqa: F401
    _HAS_QT = True
except ImportError:
    _HAS_QT = False


class _Skip(Exception):
    """Raised instead of pytest.skip when pytest itself is unavailable."""


def _require_qt():
    if not _HAS_QT:
        try:
            import pytest
        except ImportError:
            raise _Skip("PySide6 Qt modules not available")
        pytest.skip("PySide6 Qt modules not available")


# ----------------------------- parse_user_date ----------------------------- #
def test_parse_user_date_accepts_iso_shapes():
    assert parse_user_date("") == (True, None)
    assert parse_user_date(None) == (True, None)
    assert parse_user_date("2026-05-12") == (True, "2026-05-12")
    assert parse_user_date("2026-05-12 07:15") == (True, "2026-05-12T07:15")
    assert parse_user_date("2026-05-12T07:15:03") == (True, "2026-05-12T07:15:03")


def test_parse_user_date_rejects_typos():
    for bad in ("12/05/2026", "2026-5-2", "2026-05-12T7:15", "yesterday",
                "2026-05-12T07:15:03+00:00"):
        ok, norm = parse_user_date(bad)
        assert ok is False and norm is None, bad


# ----------------------------- payment_status ------------------------------ #
def test_plain_text_with_fail_word_gets_no_badge():
    assert payment_status("MESSAGE", "don't fail your exams!", None) == ""
    assert payment_status(None, "the approved plan", None) == ""


def test_msg_type_is_authoritative():
    assert payment_status("TRANSFER", "x", 50.0) == "success"
    assert payment_status("UPI_REQUEST", "", 50.0) == "requested"
    assert payment_status("UPI_REQUEST_RESPONSE", "", 50.0) == "success"
    assert payment_status("TRANSFER_FAILED", "", 50.0) == "failed"
    assert payment_status("REQUEST_DECLINED", "", 50.0) == "declined"


def test_declined_response_is_not_success():
    # real-world shape: a request that was declined arrives as *_RESPONSE with
    # the outcome only in the rendered text — must read declined, not success
    assert payment_status("UPI_RESPONSE", "Payment request declined", 50.0) == "declined"
    assert payment_status("UPI_RESPONSE", "request failed", 50.0) == "failed"
    assert payment_status("UPI_RESPONSE", "sent you money", 50.0) == "success"
    # an executed TRANSFER's free-text note must not fake a failure badge
    assert payment_status("TRANSFER", "for the failed exam retake", 50.0) == "success"


def test_content_fallback_only_in_payment_context():
    # amount present -> payment context -> content words may classify
    assert payment_status("MESSAGE", "payment failed", 10.0) == "failed"
    assert payment_status("MESSAGE", "declined by bank", 10.0) == "declined"
    assert payment_status("MESSAGE", "approved", 10.0) == "success"
    # no amount, no payment msg_type -> never classify
    assert payment_status("MESSAGE", "payment failed", None) == ""


# --------------------------- conversation_label ---------------------------- #
def test_label_prefers_resolved_counterparty():
    items = [{"chat_with": "Counterparty A", "sender_name": "X", "sender_id": "s1"}]
    assert conversation_label(items, "me", "chan_1") == "Counterparty A"


def test_label_falls_back_to_non_subject_sender():
    items = [{"sender_name": "Me", "sender_id": "me"},
             {"sender_name": "Them", "sender_id": "other"}]
    assert conversation_label(items, "me", "chan_1") == "Them"


def test_unnamed_merchant_channels_stay_distinguishable():
    items = [{"sender_name": None, "sender_id": "me"}]
    a = conversation_label(items, "me", "sendbird_group_channel_000111")
    b = conversation_label(items, "me", "sendbird_group_channel_000222")
    assert a != b, "two unnamed merchant channels must not collapse to one label"


# ------------------------ timeline filter integration ---------------------- #
def test_timeline_events_follow_filterspec():
    _require_qt()
    from paytmforensics.gui.timelineview import _to_events
    recs = [
        {"utc_iso": "2026-05-10T10:00:00+00:00", "ref_domain": "transaction",
         "summary": "debit 76.0 Test Grocer"},
        {"utc_iso": "2026-05-12T07:15:03+00:00", "ref_domain": "message",
         "summary": "msg: hello"},
        {"utc_iso": "2026-06-01T00:00:00+00:00", "ref_domain": "search",
         "summary": "query: chai"},
    ]
    spec = FilterSpec(date_from="2026-05-12", date_to="2026-05-12")
    filtered = apply_filter(recs, spec)
    events = _to_events(filtered)
    assert len(events) == 1 and events[0][1] == "message"

    spec = FilterSpec(text="grocer")
    assert len(apply_filter(recs, spec)) == 1


def test_to_events_skips_unparseable_and_sorts():
    _require_qt()
    from paytmforensics.gui.timelineview import _to_events
    recs = [
        {"utc_iso": "2026-06-01T00:00:00+00:00", "ref_domain": "b", "summary": ""},
        {"utc_iso": "not-a-date", "ref_domain": "x", "summary": ""},
        {"utc_iso": None, "ref_domain": "y", "summary": ""},
        {"utc_iso": "2026-05-01T00:00:00+00:00", "ref_domain": "a", "summary": ""},
    ]
    events = _to_events(recs)
    assert [e[1] for e in events] == ["a", "b"]


if __name__ == "__main__":
    import sys, traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except _Skip as e:
            print(f"SKIP  {fn.__name__}  ({e})")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    sys.exit(1 if failed else 0)
