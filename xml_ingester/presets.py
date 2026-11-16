"""Built-in schema presets for the supplier's v1 and v2 order feeds.

The two versions use different XML namespaces and different element shapes
(v1 is element-text based, v2 moves identity onto attributes), but both map
onto the same canonical staging columns. Callers may also build their own
``FieldMapping`` instead of using these presets.
"""

from .mapping import FieldMapping, RecordMapping
from .models import TABLE_ITEM, TABLE_ORDER, TABLE_PAYMENT

V1_NAMESPACE = "urn:supplier:orders:v1"
V2_NAMESPACE = "urn:supplier:orders:v2"


def _build_v1() -> FieldMapping:
    order = RecordMapping(
        table=TABLE_ORDER,
        root_tag="order",
        columns={
            "order_no": ["order/orderNo"],
            "customer_name": ["order/customer/name"],
            "total_amount": ["order/totalAmount"],
            "currency": ["order/currency"],
        },
    )
    item = RecordMapping(
        table=TABLE_ITEM,
        root_tag="item",
        columns={
            "line_no": ["item/lineNo"],
            "sku": ["item/sku"],
            "item_name": ["item/name"],
            "qty": ["item/qty"],
            "unit_price": ["item/unitPrice"],
        },
    )
    payment = RecordMapping(
        table=TABLE_PAYMENT,
        root_tag="payment",
        columns={
            "method": ["payment/method"],
            "amount": ["payment/amount"],
        },
    )
    return FieldMapping(
        version="v1",
        namespaces=frozenset({V1_NAMESPACE}),
        order=order,
        item=item,
        payment=payment,
    )


def _build_v2() -> FieldMapping:
    order = RecordMapping(
        table=TABLE_ORDER,
        root_tag="order",
        columns={
            "order_no": ["order/@id"],
            "customer_name": ["order/buyer/@company"],
            "total_amount": ["order/total"],
            "currency": ["order/@currency"],
        },
    )
    item = RecordMapping(
        table=TABLE_ITEM,
        root_tag="line",
        columns={
            "line_no": ["line/@seq"],
            "sku": ["line/@sku"],
            "item_name": ["line/desc"],
            "qty": ["line/quantity"],
            "unit_price": ["line/price"],
        },
    )
    payment = RecordMapping(
        table=TABLE_PAYMENT,
        root_tag="payment",
        columns={
            "method": ["payment/@channel"],
            "amount": ["payment/paidAmount"],
        },
    )
    return FieldMapping(
        version="v2",
        namespaces=frozenset({V2_NAMESPACE}),
        order=order,
        item=item,
        payment=payment,
    )


PRESETS = {
    "v1": _build_v1(),
    "v2": _build_v2(),
}


def get_preset(version: str) -> FieldMapping:
    """Return a defensive copy of a preset mapping."""
    try:
        preset = PRESETS[version]
    except KeyError:
        raise KeyError(f"unknown schema version {version!r}; expected one of {sorted(PRESETS)}")
    return FieldMapping(
        version=preset.version,
        namespaces=preset.namespaces,
        order=RecordMapping(preset.order.table, preset.order.root_tag, dict(preset.order.columns)),
        item=RecordMapping(preset.item.table, preset.item.root_tag, dict(preset.item.columns)),
        payment=RecordMapping(preset.payment.table, preset.payment.root_tag, dict(preset.payment.columns)),
    )
