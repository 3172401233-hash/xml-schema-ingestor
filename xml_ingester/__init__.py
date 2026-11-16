"""Streaming XML order feed ingester with multi-version namespace support."""

from .handler import OrderFeedHandler
from .keys import item_key, order_key, payment_key
from .mapping import FieldMapping, RecordMapping, parse_path
from .models import (
    TABLE_ITEM,
    TABLE_ORDER,
    TABLE_PAYMENT,
    IngestResult,
    StagingRecord,
)
from .parser import ingest_orders
from .presets import PRESETS, V1_NAMESPACE, V2_NAMESPACE, get_preset

__all__ = [
    "ingest_orders",
    "OrderFeedHandler",
    "FieldMapping",
    "RecordMapping",
    "parse_path",
    "StagingRecord",
    "IngestResult",
    "order_key",
    "item_key",
    "payment_key",
    "get_preset",
    "PRESETS",
    "V1_NAMESPACE",
    "V2_NAMESPACE",
    "TABLE_ORDER",
    "TABLE_ITEM",
    "TABLE_PAYMENT",
]
