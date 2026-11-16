"""Streaming SAX content handler that turns order XML into staging records.

The parser consumes the feed incrementally (never building a DOM). Records
are emitted as soon as their enclosing element closes, so a truncated file
tail only loses the unfinished elements: everything already closed survives.
"""

from typing import Any, Dict, List, Optional, Tuple

import xml.sax

from .keys import item_key, order_key, payment_key
from .mapping import FieldMapping, ParsedPath, RecordMapping
from .models import (
    TABLE_ITEM,
    TABLE_ORDER,
    TABLE_PAYMENT,
    IngestResult,
    StagingRecord,
)


class _Frame:
    __slots__ = ("tag", "ns", "text")

    def __init__(self, tag: str, ns: str) -> None:
        self.tag = tag
        self.ns = ns
        self.text: List[str] = []


class OrderFeedHandler(xml.sax.ContentHandler):
    """SAX handler producing :class:`StagingRecord` objects."""

    def __init__(self, field_mapping: FieldMapping) -> None:
        super().__init__()
        self._mapping = field_mapping
        self.result = IngestResult()

        self._stack: List[_Frame] = []
        self._order: Optional[Dict[str, Any]] = None
        self._item: Optional[Dict[str, Any]] = None
        self._payment: Optional[Dict[str, Any]] = None
        self._order_seq = 0
        self._payment_seq = 0
        self._seen_namespaces: Dict[str, str] = {}

        self._order_attrs = field_mapping.order.attribute_bindings()
        self._order_text = field_mapping.order.text_bindings()
        self._item_attrs = field_mapping.item.attribute_bindings()
        self._item_text = field_mapping.item.text_bindings()
        self._payment_attrs = field_mapping.payment.attribute_bindings()
        self._payment_text = field_mapping.payment.text_bindings()

    # ------------------------------------------------------------------ events
    def startElementNS(self, name: Tuple[Optional[str], str], qname, attrs) -> None:
        ns_uri, local = name
        ns_uri = ns_uri or ""
        self._check_namespace(ns_uri, local)
        self._stack.append(_Frame(local, ns_uri))

        if local == self._mapping.order.root_tag and self._order is None:
            self._begin_order(ns_uri, attrs)
        elif local == self._mapping.item.root_tag and self._order is not None and self._item is None:
            self._begin_item(ns_uri, attrs)
        elif local == self._mapping.payment.root_tag and self._order is not None and self._payment is None:
            self._begin_payment(ns_uri, attrs)
        else:
            self._capture_attributes(local, attrs)

    def endElementNS(self, name: Tuple[Optional[str], str], qname) -> None:
        _, local = name
        frame = self._stack[-1]
        text = "".join(frame.text).strip()

        is_record_close = (
            (self._payment is not None and local == self._mapping.payment.root_tag)
            or (self._item is not None and local == self._mapping.item.root_tag)
            or (self._order is not None and local == self._mapping.order.root_tag)
        )
        if not is_record_close:
            # Capture while the closing frame is still on the stack so the
            # relative element path still contains this element's local name.
            self._capture_text(text)
        self._stack.pop()

        if self._payment is not None and local == self._mapping.payment.root_tag:
            self._end_payment()
        elif self._item is not None and local == self._mapping.item.root_tag:
            self._end_item()
        elif self._order is not None and local == self._mapping.order.root_tag:
            self._end_order()

    def characters(self, content: str) -> None:
        if self._stack:
            self._stack[-1].text.append(content)

    # ------------------------------------------------------------- record open
    def _begin_order(self, ns_uri: str, attrs) -> None:
        self._order_seq += 1
        self._payment_seq = 0
        self._order = {
            "fields": {},
            "depth": len(self._stack),
            "ns": ns_uri,
            "seq": self._order_seq,
            "identity": f"order#{self._order_seq}",
            "items": [],
            "payments": [],
        }
        self._apply_attributes(self._order_attrs, self._mapping.order, attrs)

    def _begin_item(self, ns_uri: str, attrs) -> None:
        assert self._order is not None
        self._item = {
            "fields": {},
            "depth": len(self._stack),
            "ns": ns_uri,
            "identity": f"{self._order['identity']}/{self._mapping.item.root_tag}#{len(self._order['items'])}",
        }
        self._apply_attributes(self._item_attrs, self._mapping.item, attrs)

    def _begin_payment(self, ns_uri: str, attrs) -> None:
        assert self._order is not None
        self._payment_seq += 1
        self._payment = {
            "fields": {},
            "depth": len(self._stack),
            "ns": ns_uri,
            "seq": self._payment_seq,
        }
        self._apply_attributes(self._payment_attrs, self._mapping.payment, attrs)

    # ------------------------------------------------------------ record close
    def _end_item(self) -> None:
        assert self._order is not None and self._item is not None
        self._order["items"].append(self._item)
        self._item = None

    def _end_payment(self) -> None:
        assert self._order is not None and self._payment is not None
        self._order["payments"].append(self._payment)
        self._payment = None

    def _end_order(self) -> None:
        assert self._order is not None
        order = self._order
        self._order = None
        self._item = None
        self._payment = None

        order_fields = dict(order["fields"])
        order_no = order_fields.get("order_no")
        o_key = order_key(order_no, self._mapping.version, order["identity"])

        self.result.records.append(
            StagingRecord(
                table=TABLE_ORDER,
                record_key=o_key,
                fields=order_fields,
                schema_version=self._mapping.version,
            )
        )

        for index, item in enumerate(order["items"]):
            fields = dict(item["fields"])
            line_no = fields.get("line_no")
            identity = item["identity"] if line_no is None else ""
            key = item_key(o_key, line_no, identity or f"item#{index}")
            self.result.records.append(
                StagingRecord(
                    table=TABLE_ITEM,
                    record_key=key,
                    fields=fields,
                    parent_key=o_key,
                    schema_version=self._mapping.version,
                )
            )

        for index, payment in enumerate(order["payments"]):
            key = payment_key(o_key, index + 1)
            self.result.records.append(
                StagingRecord(
                    table=TABLE_PAYMENT,
                    record_key=key,
                    fields=dict(payment["fields"]),
                    parent_key=o_key,
                    schema_version=self._mapping.version,
                )
            )

    # ------------------------------------------------------------- value capture
    def _relative_path(self) -> ParsedPath:
        if self._payment is not None:
            base_depth = self._payment["depth"] - 1
        elif self._item is not None:
            base_depth = self._item["depth"] - 1
        else:
            base_depth = self._order["depth"] - 1  # type: ignore[index]
        return tuple(frame.tag for frame in self._stack[base_depth:])

    def _capture_text(self, text: str) -> None:
        if not text or self._order is None:
            return
        path = self._relative_path()
        binding, fields = self._active_text_binding()
        for column in binding.get(path, ()):
            self._set_first(fields, column, text)

    def _capture_attributes(self, local: str, attrs) -> None:
        if self._order is None:
            return
        path = self._relative_path()
        binding, fields = self._active_attribute_binding()
        for attr_path, columns in binding.items():
            if attr_path[:-1] != path:
                continue
            attr_local = attr_path[-1][1:]
            value = self._attr_value(attrs, attr_local)
            if value is not None:
                for column in columns:
                    self._set_first(fields, column, value)

    def _apply_attributes(self, binding, record_mapping: RecordMapping, attrs) -> None:
        # Root element attributes: relative path is just (root_tag, @attr).
        root = record_mapping.root_tag
        fields = self._active_fields()
        for attr_path, columns in binding.items():
            if len(attr_path) != 2 or attr_path[0] != root:
                continue
            value = self._attr_value(attrs, attr_path[-1][1:])
            if value is not None:
                for column in columns:
                    self._set_first(fields, column, value)

    @staticmethod
    def _attr_value(attrs, attr_local: str) -> Optional[str]:
        # attrs is keyed by (namespace_uri, localname); attribute names in the
        # mappings are local names, so match on the second tuple element.
        for (uri, local), value in attrs.items():
            if local == attr_local:
                return value
        return None

    @staticmethod
    def _set_first(fields: Dict[str, Any], column: str, value: str) -> None:
        # First-match-wins: candidate paths are listed in priority order and a
        # column already populated must not be overwritten by a later path.
        fields.setdefault(column, value)

    def _active_fields(self) -> Dict[str, Any]:
        if self._payment is not None:
            return self._payment["fields"]
        if self._item is not None:
            return self._item["fields"]
        assert self._order is not None
        return self._order["fields"]

    def _active_text_binding(self):
        if self._payment is not None:
            return self._payment_text, self._payment["fields"]
        if self._item is not None:
            return self._item_text, self._item["fields"]
        assert self._order is not None
        return self._order_text, self._order["fields"]

    def _active_attribute_binding(self):
        if self._payment is not None:
            return self._payment_attrs, self._payment["fields"]
        if self._item is not None:
            return self._item_attrs, self._item["fields"]
        assert self._order is not None
        return self._order_attrs, self._order["fields"]

    # ------------------------------------------------------------ ns validation
    def _check_namespace(self, ns_uri: str, local: str) -> None:
        expected = self._mapping.namespaces
        if not expected or ns_uri in expected:
            return
        prior = self._seen_namespaces.get(ns_uri)
        if prior is None:
            self._seen_namespaces[ns_uri] = local
            expected_uris = ", ".join(sorted(expected)) or "(any)"
            self.result.warnings.append(
                f"unexpected namespace {ns_uri or '(none)'} on <{local}>; "
                f"schema {self._mapping.version} expects {expected_uris}"
            )
