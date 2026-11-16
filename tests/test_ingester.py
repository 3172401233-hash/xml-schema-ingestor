import io
import unittest

from xml_ingester import (
    TABLE_ITEM,
    TABLE_ORDER,
    TABLE_PAYMENT,
    FieldMapping,
    RecordMapping,
    ingest_orders,
    item_key,
    order_key,
    payment_key,
)
from xml_ingester.presets import V1_NAMESPACE, V2_NAMESPACE, get_preset

from .support import ChunkedReader, OneByteReader, read_sample, sample_path


def _by_table(result, table):
    return [record for record in result.records if record.table == table]


class V1IngestionTests(unittest.TestCase):
    def test_parses_orders_items_and_optional_payment(self):
        with open(sample_path("orders_v1.xml"), "rb") as stream:
            result = ingest_orders(stream, "v1")

        self.assertTrue(result.complete)
        self.assertFalse(result.truncated)
        self.assertIsNone(result.error)

        orders = _by_table(result, TABLE_ORDER)
        items = _by_table(result, TABLE_ITEM)
        payments = _by_table(result, TABLE_PAYMENT)
        self.assertEqual(len(orders), 2)
        self.assertEqual(len(items), 3)
        self.assertEqual(len(payments), 1)

        first_order = orders[0]
        self.assertEqual(
            first_order.fields,
            {
                "order_no": "SO-1001",
                "customer_name": "Acme & Co",
                "total_amount": "125.50",
                "currency": "CNY",
            },
        )
        self.assertEqual(first_order.schema_version, "v1")
        self.assertEqual(items[0].fields["item_name"], "Widget <Pro>")

        # Second order has no payment node: no payment record is emitted.
        second_order = orders[1]
        second_children = [r for r in result.records if r.parent_key == second_order.record_key]
        self.assertEqual([r.table for r in second_children], [TABLE_ITEM])

    def test_escapes_are_decoded(self):
        with open(sample_path("orders_v1.xml"), "rb") as stream:
            result = ingest_orders(stream, "v1")
        first_order = result.orders[0]
        self.assertIn("&", first_order.fields["customer_name"])
        self.assertEqual(first_order.fields["customer_name"], "Acme & Co")
        self.assertEqual(result.items[0].fields["item_name"], "Widget <Pro>")


class V2IngestionTests(unittest.TestCase):
    def test_v2_namespace_and_attribute_based_mapping(self):
        with open(sample_path("orders_v2.xml"), "rb") as stream:
            result = ingest_orders(stream, "v2")

        self.assertTrue(result.complete)
        orders = result.orders
        self.assertEqual(len(orders), 2)
        self.assertEqual(orders[0].fields["order_no"], "SO-9001")
        self.assertEqual(orders[0].fields["customer_name"], "Stark Industries")
        self.assertEqual(orders[0].fields["currency"], "EUR")

        items = result.items
        self.assertEqual(items[0].fields["sku"], "SKU-V2-1")
        self.assertEqual(items[1].fields["line_no"], "20")
        self.assertEqual(items[0].fields["item_name"], "Reactor & Coil")

        payment = result.payments[0]
        self.assertEqual(payment.fields["method"], "WIRE")
        self.assertEqual(payment.fields["amount"], "240.00")

    def test_v1_mapping_rejects_v2_namespace_with_warning(self):
        # Same bytes, wrong declared schema: values still extract (mapping is
        # local-name based) but the namespace mismatch must be reported.
        with open(sample_path("orders_v2.xml"), "rb") as stream:
            result = ingest_orders(stream, "v1")
        self.assertTrue(any(V2_NAMESPACE in warning for warning in result.warnings))
        self.assertTrue(any("v1" in warning for warning in result.warnings))


class ParentChildRelationTests(unittest.TestCase):
    def test_items_and_payments_link_to_their_order(self):
        with open(sample_path("orders_v1.xml"), "rb") as stream:
            result = ingest_orders(stream, "v1")

        order_keys = {order.record_key for order in result.orders}
        for child in result.items + result.payments:
            self.assertIn(child.parent_key, order_keys)
            self.assertNotEqual(child.record_key, child.parent_key)

        first_order_key = result.orders[0].record_key
        first_children = [r for r in result.records if r.parent_key == first_order_key]
        self.assertEqual(len(first_children), 3)  # 2 items + 1 payment
        tables = sorted(r.table for r in first_children)
        self.assertEqual(tables, [TABLE_ITEM, TABLE_ITEM, TABLE_PAYMENT])


class StableKeyTests(unittest.TestCase):
    def test_duplicate_order_nodes_produce_identical_keys(self):
        with open(sample_path("orders_v1_duplicate.xml"), "rb") as stream:
            result = ingest_orders(stream, "v1")

        orders = result.orders
        self.assertEqual(len(orders), 2)
        self.assertEqual(orders[0].record_key, orders[1].record_key)

        items = result.items
        payments = result.payments
        self.assertEqual(items[0].record_key, items[1].record_key)
        self.assertEqual(payments[0].record_key, payments[1].record_key)

    def test_keys_are_stable_across_separate_runs_and_files(self):
        with open(sample_path("orders_v1.xml"), "rb") as stream:
            run_one = ingest_orders(stream, "v1")
        with open(sample_path("orders_v1_duplicate.xml"), "rb") as stream:
            run_two = ingest_orders(stream, "v1")

        self.assertEqual(run_one.orders[0].record_key, run_two.orders[0].record_key)
        self.assertEqual(run_one.items[0].record_key, run_two.items[0].record_key)
        self.assertEqual(run_one.payments[0].record_key, run_two.payments[0].record_key)

    def test_key_functions_are_deterministic(self):
        key_a = order_key("SO-1", "v1", "order#1")
        key_b = order_key("SO-1", "v1", "order#9")
        self.assertEqual(key_a, key_b)
        self.assertTrue(key_a.startswith("ord_"))

        ok = order_key("SO-1", "v1", "order#1")
        self.assertEqual(item_key(ok, "1", "x"), item_key(ok, "1", "y"))
        self.assertEqual(payment_key(ok, 1), payment_key(ok, 1))
        self.assertNotEqual(payment_key(ok, 1), payment_key(ok, 2))


class TruncationTests(unittest.TestCase):
    def test_truncated_tail_keeps_closed_orders(self):
        with open(sample_path("orders_v1_truncated.xml"), "rb") as stream:
            result = ingest_orders(stream, "v1")

        self.assertFalse(result.complete)
        self.assertTrue(result.truncated)
        self.assertIsNotNone(result.error)
        # The first order fully closed before the feed was cut.
        self.assertEqual(len(result.orders), 1)
        self.assertEqual(result.orders[0].fields["order_no"], "SO-2001")
        self.assertEqual(len(result.items), 1)
        self.assertEqual(len(result.payments), 1)

    def test_truncation_inside_an_item_drops_only_that_order(self):
        # One complete order, then a second order whose item never closes.
        xml = (
            b'<orders xmlns="' + V1_NAMESPACE.encode() + b'">'
            b"<order><orderNo>A1</orderNo><item><lineNo>1</lineNo></item></order>"
            b"<order><orderNo>A2</orderNo><item><lineNo>9</lineNo><sku>PARTIAL"
        )
        result = ingest_orders(io.BytesIO(xml), "v1")
        self.assertTrue(result.truncated)
        self.assertEqual([o.fields["order_no"] for o in result.orders], ["A1"])

    def test_garbled_tail_is_fatal_but_retains_complete_records(self):
        with open(sample_path("orders_v1_garbled_tail.xml"), "rb") as stream:
            result = ingest_orders(stream, "v1")
        self.assertFalse(result.complete)
        self.assertFalse(result.truncated)  # junk after root, not a cut feed
        self.assertIn("junk after document element", result.error)
        self.assertEqual(len(result.orders), 1)
        self.assertEqual(result.orders[0].fields["order_no"], "SO-3001")

    def test_broken_entity_reference_mid_feed(self):
        xml = (
            b'<orders xmlns="' + V1_NAMESPACE.encode() + b'">'
            b"<order><orderNo>B1</orderNo></order>"
            b"<order><orderNo>B2</orderNo><customer><name>bad &am"
        )
        result = ingest_orders(io.BytesIO(xml), "v1")
        self.assertTrue(result.truncated)
        self.assertEqual([o.fields["order_no"] for o in result.orders], ["B1"])


class StreamingTests(unittest.TestCase):
    def test_empty_stream_is_reported_as_truncated(self):
        result = ingest_orders(io.BytesIO(b""), "v1")
        self.assertFalse(result.complete)
        self.assertTrue(result.truncated)
        self.assertEqual(result.records, [])

    def test_chunked_binary_stream_matches_whole_file(self):
        data = read_sample("orders_v1.xml")
        chunked = ingest_orders(io.BufferedReader(ChunkedReader(data, chunk_size=7)), "v1")
        whole = ingest_orders(io.BytesIO(data), "v1")

        self.assertTrue(chunked.complete)
        self.assertEqual(
            [r.to_dict() for r in chunked.records],
            [r.to_dict() for r in whole.records],
        )

    def test_one_byte_at_a_time_stream(self):
        data = read_sample("orders_v2.xml")
        reader = OneByteReader(data)
        result = ingest_orders(io.BufferedReader(reader), "v2")
        self.assertTrue(result.complete)
        self.assertEqual(len(result.orders), 2)
        self.assertGreater(reader.read_call_count, len(data) // 2)

    def test_text_stream_is_supported(self):
        data = read_sample("orders_v1.xml").decode("utf-8")
        result = ingest_orders(io.StringIO(data), "v1")
        self.assertTrue(result.complete)
        self.assertEqual(len(result.orders), 2)
        self.assertEqual(result.orders[0].fields["customer_name"], "Acme & Co")

    def test_real_file_object_streaming(self):
        with open(sample_path("orders_v1.xml"), "rb") as stream:
            result = ingest_orders(stream, "v1")
        self.assertTrue(result.complete)
        self.assertEqual(len(result.records), 6)

    def test_invalid_stream_raises_type_error(self):
        with self.assertRaises(TypeError):
            ingest_orders(b"<not a stream/>", "v1")


class OptionalFieldTests(unittest.TestCase):
    def test_missing_optional_fields_are_simply_absent(self):
        xml = (
            b'<orders xmlns="' + V1_NAMESPACE.encode() + b'">'
            b"<order><orderNo>C1</orderNo>"
            b"<item><sku>ONLY-SKU</sku></item></order></orders>"
        )
        result = ingest_orders(io.BytesIO(xml), "v1")
        self.assertTrue(result.complete)
        order = result.orders[0]
        item = result.items[0]
        self.assertEqual(order.fields["order_no"], "C1")
        self.assertNotIn("customer_name", order.fields)
        self.assertNotIn("currency", order.fields)
        self.assertEqual(item.fields["sku"], "ONLY-SKU")
        self.assertNotIn("line_no", item.fields)
        self.assertEqual(len(result.payments), 0)

    def test_empty_elements_do_not_overwrite_values(self):
        xml = (
            b'<orders xmlns="' + V1_NAMESPACE.encode() + b'">'
            b"<order><orderNo>D1</orderNo><currency></currency>"
            b"<totalAmount> </totalAmount></order></orders>"
        )
        result = ingest_orders(io.BytesIO(xml), "v1")
        order = result.orders[0]
        self.assertEqual(order.fields["order_no"], "D1")
        self.assertNotIn("currency", order.fields)
        self.assertNotIn("total_amount", order.fields)


class CustomMappingTests(unittest.TestCase):
    def test_custom_field_mapping_takes_precedence(self):
        mapping = FieldMapping(
            version="custom",
            namespaces=frozenset({"urn:custom:ns"}),
            order=RecordMapping(
                table=TABLE_ORDER,
                root_tag="order",
                columns={"ref": ["order/ref"], "money": ["order/amount", "order/fallbackAmount"]},
            ),
            item=RecordMapping(table=TABLE_ITEM, root_tag="line", columns={"code": ["line/code"]}),
            payment=RecordMapping(table=TABLE_PAYMENT, root_tag="pay", columns={"via": ["pay/via"]}),
        )
        xml = (
            b'<feed xmlns="urn:custom:ns">'
            b"<order><ref>R-1</ref><fallbackAmount>42</fallbackAmount>"
            b"<line><code>X</code></line><pay><via>CARD</via></pay></order></feed>"
        )
        result = ingest_orders(io.BytesIO(xml), "ignored-version", field_mapping=mapping)
        self.assertTrue(result.complete)
        order = result.orders[0]
        self.assertEqual(order.schema_version, "custom")
        self.assertEqual(order.fields, {"ref": "R-1", "money": "42"})
        self.assertEqual(result.items[0].fields, {"code": "X"})
        self.assertEqual(result.payments[0].fields, {"via": "CARD"})

    def test_unknown_preset_raises_key_error(self):
        with self.assertRaises(KeyError):
            ingest_orders(io.BytesIO(b"<x/>"), "v99")

    def test_first_candidate_path_wins(self):
        mapping = get_preset("v1")
        mapping.order.columns["order_no"] = ["order/orderNo", "order/legacyNo"]
        xml = (
            b'<orders xmlns="' + V1_NAMESPACE.encode() + b'">'
            b"<order><orderNo>PRIMARY</orderNo><legacyNo>SECONDARY</legacyNo></order></orders>"
        )
        result = ingest_orders(io.BytesIO(xml), "v1", field_mapping=mapping)
        self.assertEqual(result.orders[0].fields["order_no"], "PRIMARY")


if __name__ == "__main__":
    unittest.main()
