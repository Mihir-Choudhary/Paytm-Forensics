"""Enum decode tables for Paytm passbook + WorkManager integer codes.

Values were derived from observed data in the reference extraction and the decompiled app.
Where a code is uncertain it is mapped to f"code:<n>" rather than guessed.
"""
from __future__ import annotations

from typing import Any, Optional

# passbook.UthListingEntity.txnIndicator
TXN_INDICATOR = {1: "credit", 2: "debit"}

# passbook.UthListingEntity.statusKey  (2 = success observed; others conservative)
STATUS_KEY = {1: "pending", 2: "success", 3: "failed", 4: "refunded"}

# passbook.UthListingEntity.txnCategory
# Verified against the app's own txnTag column on a real extraction:
#   1 -> 39/39 rows tagged "Food"          => food_and_beverages (confirmed)
#   2 -> 3/4 rows tagged "Money Received", 1 tagged "Cashback"
#        => category 2 is broader than cashback; label it for what it is and let the
#           authoritative `tag` column (now displayed) carry the app's own wording.
TXN_CATEGORY = {1: "food_and_beverages", 2: "money_in", 9: "cashback"}

# androidx WorkManager WorkInfo.State
WORK_STATE = {0: "ENQUEUED", 1: "RUNNING", 2: "SUCCEEDED", 3: "FAILED",
              4: "BLOCKED", 5: "CANCELLED"}


def _lookup(table: dict, value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return table.get(int(value), f"code:{value}")
    except (ValueError, TypeError):
        return f"code:{value}"


def txn_direction(v: Any) -> Optional[str]:
    return _lookup(TXN_INDICATOR, v)


def txn_status(v: Any) -> Optional[str]:
    return _lookup(STATUS_KEY, v)


def txn_category(v: Any) -> Optional[str]:
    return _lookup(TXN_CATEGORY, v)


def work_state(v: Any) -> Optional[str]:
    return _lookup(WORK_STATE, v)
