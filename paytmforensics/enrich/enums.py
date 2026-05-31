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

# passbook.UthListingEntity.txnCategory (observed: 1 food, 2/9 cashback)
TXN_CATEGORY = {1: "food_and_beverages", 2: "cashback", 9: "cashback"}

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
