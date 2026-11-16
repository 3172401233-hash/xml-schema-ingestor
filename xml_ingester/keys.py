"""Deterministic, stable primary-key generation for staging records.

Keys are pure functions of the business identity of a record. Re-parsing the
same order -- even when the order node appears multiple times in a feed or
across separate daily files -- always produces the same key, which lets the
staging loader upsert idempotently.
"""

import hashlib
from typing import Optional

from .models import TABLE_ITEM, TABLE_ORDER, TABLE_PAYMENT


def _digest(parts: object) -> str:
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def order_key(order_no: Optional[str], schema_version: str, fallback_identity: str) -> str:
    """Stable key for an order.

    Prefers the supplier order number; when it is absent (an optional field
    that never showed up) falls back to a digest of the element's structural
    identity so the key still stays deterministic for the same input bytes.
    """
    if order_no:
        return f"ord_{_digest((schema_version, order_no))}"
    return f"ord_{_digest((schema_version, 'fallback', fallback_identity))}"


def item_key(order_no_key: str, line_no: Optional[str], item_identity: str) -> str:
    """Stable key for an order line item.

    Prefers the supplier line number when present; otherwise digests the item
    element's structural path inside the order so repeated ingestion yields
    the same key.
    """
    if line_no:
        return f"itm_{_digest((order_no_key, line_no))}"
    return f"itm_{_digest((order_no_key, 'path', item_identity))}"


def payment_key(order_no_key: str, seq: int) -> str:
    """Stable key for the optional payment node (a file may carry several)."""
    return f"pay_{_digest((order_no_key, seq))}"


__all__ = ["order_key", "item_key", "payment_key", "TABLE_ORDER", "TABLE_ITEM", "TABLE_PAYMENT"]
