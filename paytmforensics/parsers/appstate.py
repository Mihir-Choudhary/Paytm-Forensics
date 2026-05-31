"""App-state parsers: Paytm Mall cart (app_data/common_storage) + Crashlytics sessions."""
from __future__ import annotations

import json
import os
import re
from typing import Iterator

from .base import BaseParser, register
from ..core.models import AppStateItem, CrashSession, Record
from ..enrich import timestamps


@register
class CommonStorageParser(BaseParser):
    """Paytm Mall shopping cart stored as a Java-serialized HashMap (common_storage).

    The serialized blob embeds a `cartResponse` JSON string; we extract it and surface the
    cart summary (id, item count, customer, checkout URL, response date)."""
    name = "appstate.cart"
    needs = ("common_storage",)

    def parse(self) -> Iterator[Record]:
        art = self.get("common_storage")
        if not art:
            return
        try:
            with open(art.abs_path, "rb") as f:
                txt = f.read().decode("utf-8", "ignore")
        except OSError:
            return
        idx = txt.find("cartResponse")
        start = txt.find("{", idx if idx >= 0 else 0)
        if start < 0:
            return
        try:
            obj, _end = json.JSONDecoder().raw_decode(txt[start:])
        except (ValueError, json.JSONDecodeError):
            return
        cart = (obj.get("cart") or {}) if isinstance(obj, dict) else {}
        net = (obj.get("networkResponse") or {}) if isinstance(obj, dict) else {}
        items = cart.get("cart_items") or []
        date = (net.get("headers") or {}).get("date")
        yield AppStateItem(
            provenance=self.prov(art, None),
            raw={"cart": cart},
            key="paytm_mall_cart",
            value=(f"cart_id={cart.get('cart_id')} count={cart.get('count')} "
                   f"items={len(items)} customer_id={cart.get('customer_id')} "
                   f"order_total={cart.get('order_total')}"),
            detail=cart.get("place_order_url"),
            timestamp={"raw": date, "epoch_type": "http_date", "utc_iso": _http_date(date)},
        )


def _http_date(s):
    if not s:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).isoformat()
    except Exception:
        return None


@register
class CrashlyticsParser(BaseParser):
    """Crashlytics open-session metadata (files/.crashlytics.v3/.../open-sessions/<id>/)."""
    name = "crash.sessions"
    needs = ()

    def __init__(self, artifacts, hashes, all_artifacts=None):
        super().__init__(artifacts, hashes, all_artifacts)
        # group artifacts by their session directory
        self._sessions: dict[str, dict] = {}
        for a in self.all_artifacts:
            low = a.rel_path.replace("\\", "/")
            m = re.search(r"\.crashlytics\.v3/.+/open-sessions/([^/]+)/(.+)$", low)
            if m:
                self._sessions.setdefault(m.group(1), {})[os.path.basename(low)] = a

    def available(self) -> bool:
        return bool(self._sessions)

    def parse(self) -> Iterator[Record]:
        for sid, files in self._sessions.items():
            uid = None
            ud = files.get("user-data")
            if ud:
                try:
                    uid = json.load(open(ud.abs_path, encoding="utf-8")).get("userId")
                except Exception:
                    uid = None
            start = None
            st = files.get("start-time")
            if st:
                try:
                    start = timestamps.decode(open(st.abs_path, encoding="utf-8").read().strip()).to_dict()
                except Exception:
                    start = None
            preview = None
            kf = files.get("keys") or files.get("report")
            if kf:
                try:
                    preview = open(kf.abs_path, encoding="utf-8", errors="ignore").read()[:200]
                except Exception:
                    preview = None
            anchor = ud or st or next(iter(files.values()))
            yield CrashSession(
                provenance=self.prov(anchor, None),
                raw={}, session_id=sid, user_id=uid, start_time=start, preview=preview,
            )
