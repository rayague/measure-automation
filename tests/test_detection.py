from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from boundary_analyzer.detection.db_table_extractor import (
    _detect_db_system,
    _extract_nosql_entities,
    _extract_tables_from_sql,
    _get_tag_value,
    _is_db_span,
    _parse_tags,
    _unquote_sql_identifier,
    extract_db_operations,
    save_db_operations_csv,
)
from boundary_analyzer.detection.endpoint_extractor import (
    extract_endpoints,
    save_endpoints_csv,
)
from boundary_analyzer.detection.endpoint_normalizer import (
    _extract_http_method,
    _extract_http_route,
    _normalize_dynamic_parameters,
    build_endpoint_key,
    extract_tags_from_span,
)
from boundary_analyzer.detection.mapping_builder import (
    _build_endpoint_lookup,
    _build_span_lookup,
    _find_endpoint_for_db_span,
    build_endpoint_table_mapping,
    save_endpoint_table_map_csv,
)


class EndpointNormalizerTest(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None

    # ---- _extract_http_method ----

    def test_extract_http_method_from_tag(self):
        tags = [{"key": "http.method", "value": "post"}]
        self.assertEqual(_extract_http_method("", tags), "POST")

    def test_extract_http_method_from_operation(self):
        tags = []
        self.assertEqual(_extract_http_method("GET /orders", tags), "GET")

    def test_extract_http_method_default(self):
        tags = []
        self.assertEqual(_extract_http_method("something", tags), "")

    def test_extract_http_method_tag_overrides_operation(self):
        tags = [{"key": "http.method", "value": "PUT"}]
        self.assertEqual(_extract_http_method("GET /orders", tags), "PUT")

    # ---- _extract_http_route ----

    def test_extract_http_route_from_tag(self):
        tags = [{"key": "http.route", "value": "/orders/{id}"}]
        self.assertEqual(_extract_http_route("GET /orders/123", tags), "/orders/{id}")

    def test_extract_http_route_from_target(self):
        tags = [{"key": "http.target", "value": "/orders/123?id=1"}]
        self.assertEqual(_extract_http_route("", tags), "/orders/123?id=1")

    def test_extract_http_route_from_url(self):
        tags = [{"key": "http.url", "value": "http://example.com/orders/123"}]
        self.assertEqual(_extract_http_route("", tags), "/orders/123")

    def test_extract_http_route_from_operation(self):
        tags = []
        self.assertEqual(_extract_http_route("DELETE /carts/5", tags), "/carts/5")

    def test_extract_http_route_fallback_operation(self):
        tags = []
        self.assertEqual(_extract_http_route("SomeRandomOperation", tags), "SomeRandomOperation")

    # ---- _normalize_dynamic_parameters ----

    def test_normalize_numeric_id(self):
        self.assertEqual(_normalize_dynamic_parameters("/orders/123"), "/orders/{id}")

    def test_normalize_multiple_ids(self):
        self.assertEqual(
            _normalize_dynamic_parameters("/products/456/reviews/789"),
            "/products/{id}/reviews/{id}",
        )

    def test_normalize_uuid_pattern(self):
        self.assertEqual(
            _normalize_dynamic_parameters("/users/550e8400-e29b-41d4-a716-446655440000/profile"),
            "/users/{uuid}/profile",
        )

    def test_normalize_no_changes(self):
        self.assertEqual(_normalize_dynamic_parameters("/orders"), "/orders")

    def test_normalize_preserves_valid_segments(self):
        self.assertEqual(_normalize_dynamic_parameters("/employees"), "/employees")

    def test_normalize_empty_string(self):
        self.assertEqual(_normalize_dynamic_parameters(""), "")

    # ---- build_endpoint_key ----

    def test_build_endpoint_key_basic(self):
        tags = [{"key": "http.method", "value": "GET"}, {"key": "http.route", "value": "/orders/{id}"}]
        self.assertEqual(build_endpoint_key("GET /orders/123", tags), "GET /orders/{id}")

    def test_build_endpoint_key_normalize_enabled(self):
        tags = [{"key": "http.method", "value": "GET"}]
        result = build_endpoint_key("GET /orders/123", tags, normalize=True)
        self.assertEqual(result, "GET /orders/{id}")

    def test_build_endpoint_key_normalize_disabled(self):
        tags = [{"key": "http.method", "value": "GET"}]
        result = build_endpoint_key("GET /orders/123", tags, normalize=False)
        self.assertEqual(result, "GET /orders/123")

    def test_build_endpoint_key_no_method(self):
        tags = []
        result = build_endpoint_key("/orders/123", tags, normalize=True)
        self.assertEqual(result, "/orders/{id}")

    def test_build_endpoint_key_no_method_no_route(self):
        tags = []
        result = build_endpoint_key("SomeRandomOp", tags)
        self.assertEqual(result, "SomeRandomOp")

    # ---- extract_tags_from_span ----

    def test_extract_tags_jaeger_format(self):
        span = {"tags": [{"key": "http.method", "value": "GET"}]}
        self.assertEqual(extract_tags_from_span(span), [{"key": "http.method", "value": "GET"}])

    def test_extract_tags_otel_format(self):
        span = {"attributes": {"http.method": "GET", "http.route": "/orders"}}
        result = extract_tags_from_span(span)
        self.assertIn({"key": "http.method", "value": "GET"}, result)
        self.assertIn({"key": "http.route", "value": "/orders"}, result)

    def test_extract_tags_empty(self):
        self.assertEqual(extract_tags_from_span({}), [])

    def test_extract_tags_tags_preferred(self):
        span = {
            "tags": [{"key": "http.method", "value": "POST"}],
            "attributes": {"http.method": "GET"},
        }
        self.assertEqual(extract_tags_from_span(span), [{"key": "http.method", "value": "POST"}])


class EndpointExtractorTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_endpoint_extractor_"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _make_span(self, operation_name, tags=None, service_name="svc", span_id="s1", trace_id="t1"):
        return {
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": None,
            "service_name": service_name,
            "operation_name": operation_name,
            "start_time": 1000,
            "duration": 50,
            "tags": json.dumps(tags) if tags else "",
        }

    def test_extract_endpoints_empty_df(self):
        df = pd.DataFrame()
        result = extract_endpoints(df)
        self.assertTrue(result.empty)
        self.assertListEqual(list(result.columns), ["service_name", "endpoint_key", "span_id", "trace_id"])

    def test_extract_endpoints_no_endpoint_spans(self):
        df = pd.DataFrame([self._make_span("internal_op")])
        result = extract_endpoints(df)
        self.assertTrue(result.empty)
        self.assertListEqual(list(result.columns), ["service_name", "endpoint_key", "span_id", "trace_id"])

    def test_extract_endpoints_with_http_method_tag(self):
        tags = [{"key": "http.method", "value": "GET"}, {"key": "http.route", "value": "/orders"}]
        df = pd.DataFrame([self._make_span("GET /orders", tags)])
        result = extract_endpoints(df)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["endpoint_key"], "GET /orders")

    def test_extract_endpoints_fallback_operation_name(self):
        df = pd.DataFrame([self._make_span("GET /orders")])
        result = extract_endpoints(df)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["endpoint_key"], "GET /orders")

    def test_extract_endpoints_fallback_normalizes_dynamic(self):
        df = pd.DataFrame([self._make_span("GET /orders/123")])
        result = extract_endpoints(df)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["endpoint_key"], "GET /orders/{id}")

    def test_extract_endpoints_excludes_health_routes(self):
        df = pd.DataFrame([self._make_span("GET /health")])
        result = extract_endpoints(df, exclude_health_routes=True)
        self.assertTrue(result.empty)

    def test_extract_endpoints_includes_health_routes_when_disabled(self):
        df = pd.DataFrame([self._make_span("GET /health")])
        result = extract_endpoints(df, exclude_health_routes=False)
        self.assertEqual(len(result), 1)

    def test_extract_endpoints_excludes_http_client_spans(self):
        df = pd.DataFrame([self._make_span("GET /students/ http send")])
        result = extract_endpoints(df, exclude_http_client_spans=True)
        self.assertTrue(result.empty)

    def test_extract_endpoints_includes_http_client_spans_when_disabled(self):
        df = pd.DataFrame([self._make_span("GET /students/ http send")])
        result = extract_endpoints(df, exclude_http_client_spans=False)
        self.assertEqual(len(result), 1)

    def test_extract_endpoints_normalize_disabled(self):
        df = pd.DataFrame([self._make_span("GET /orders/123")])
        result = extract_endpoints(df, normalize=False)
        self.assertEqual(result.iloc[0]["endpoint_key"], "GET /orders/123")

    def test_extract_endpoints_bad_tags_json(self):
        # tags set to a raw invalid string that won't parse as JSON list-of-dicts
        df = pd.DataFrame([{
            "trace_id": "t1",
            "span_id": "s1",
            "parent_span_id": None,
            "service_name": "svc1",
            "operation_name": "GET /orders",
            "start_time": 1000,
            "duration": 50,
            "tags": "not valid json",
        }])
        result = extract_endpoints(df)
        self.assertEqual(len(result), 1)
        self.assertTrue(result.iloc[0]["endpoint_key"].startswith("GET"))

    def test_save_endpoints_csv(self):
        df = pd.DataFrame({
            "service_name": ["svc1"],
            "endpoint_key": ["GET /orders"],
            "span_id": ["s1"],
            "trace_id": ["t1"],
        })
        path = self.test_dir / "endpoints.csv"
        save_endpoints_csv(df, path)
        self.assertTrue(path.exists())
        loaded = pd.read_csv(path)
        self.assertEqual(len(loaded), 1)

    def test_save_endpoints_csv_creates_dirs(self):
        df = pd.DataFrame({
            "service_name": ["svc1"],
            "endpoint_key": ["GET /orders"],
            "span_id": ["s1"],
            "trace_id": ["t1"],
        })
        path = self.test_dir / "sub" / "nested" / "endpoints.csv"
        save_endpoints_csv(df, path)
        self.assertTrue(path.exists())
        loaded = pd.read_csv(path)
        self.assertEqual(len(loaded), 1)


class DbTableExtractorTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_db_table_"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # ---- _parse_tags ----

    def test_parse_tags_json_string(self):
        self.assertEqual(_parse_tags('[{"key":"db.system","value":"postgresql"}]'), [{"key": "db.system", "value": "postgresql"}])

    def test_parse_tags_none(self):
        self.assertEqual(_parse_tags(None), [])

    def test_parse_tags_nan(self):
        self.assertEqual(_parse_tags(float("nan")), [])

    def test_parse_tags_empty_string(self):
        self.assertEqual(_parse_tags(""), [])

    def test_parse_tags_invalid_json(self):
        self.assertEqual(_parse_tags("{bad"), [])

    def test_parse_tags_already_list(self):
        tags = [{"key": "db.system", "value": "mysql"}]
        self.assertEqual(_parse_tags(tags), tags)

    # ---- _get_tag_value ----

    def test_get_tag_value_found(self):
        tags = [{"key": "db.system", "value": "postgresql"}]
        self.assertEqual(_get_tag_value(tags, "db.system"), "postgresql")

    def test_get_tag_value_not_found(self):
        tags = [{"key": "db.system", "value": "postgresql"}]
        self.assertIsNone(_get_tag_value(tags, "db.statement"))

    def test_get_tag_value_none_value(self):
        tags = [{"key": "db.system", "value": None}]
        self.assertIsNone(_get_tag_value(tags, "db.system"))

    # ---- _unquote_sql_identifier ----

    def test_unquote_backtick(self):
        self.assertEqual(_unquote_sql_identifier("`orders`"), "orders")

    def test_unquote_double_quote(self):
        self.assertEqual(_unquote_sql_identifier('"orders"'), "orders")

    def test_unquote_bracket(self):
        self.assertEqual(_unquote_sql_identifier("[orders]"), "orders")

    def test_unquote_no_quotes(self):
        self.assertEqual(_unquote_sql_identifier("orders"), "orders")

    def test_unquote_short_string(self):
        self.assertEqual(_unquote_sql_identifier("a"), "a")

    # ---- _extract_tables_from_sql ----

    def test_extract_tables_from_select(self):
        sql = "SELECT * FROM orders"
        self.assertEqual(_extract_tables_from_sql(sql), ["orders"])

    def test_extract_tables_from_join(self):
        sql = "SELECT * FROM orders JOIN users ON orders.user_id = users.id"
        self.assertEqual(_extract_tables_from_sql(sql), ["orders", "users"])

    def test_extract_tables_update(self):
        sql = "UPDATE products SET price = 10 WHERE id = 1"
        self.assertEqual(_extract_tables_from_sql(sql), ["products"])

    def test_extract_tables_insert_into(self):
        sql = "INSERT INTO orders (id, name) VALUES (1, 'test')"
        self.assertEqual(_extract_tables_from_sql(sql), ["orders"])

    def test_extract_tables_with_schema(self):
        sql = "SELECT * FROM public.orders"
        self.assertEqual(_extract_tables_from_sql(sql), ["orders"])

    def test_extract_tables_quoted(self):
        sql = 'SELECT * FROM "Order Details"'
        self.assertEqual(_extract_tables_from_sql(sql), ["order details"])

    def test_extract_tables_ignores_keywords(self):
        sql = "SELECT * FROM (SELECT 1) AS t WHERE t.a IN (SELECT 2)"
        result = _extract_tables_from_sql(sql)
        for kw in ["select", "where", "union"]:
            self.assertNotIn(kw, result)

    def test_extract_tables_excludes_system_tables(self):
        sql = "SELECT * FROM pg_catalog WHERE 1=1"
        self.assertEqual(_extract_tables_from_sql(sql), [])

    def test_extract_tables_none_input(self):
        self.assertEqual(_extract_tables_from_sql(None), [])

    def test_extract_tables_empty_string(self):
        self.assertEqual(_extract_tables_from_sql(""), [])

    # ---- _extract_nosql_entities ----

    def test_extract_nosql_mongodb(self):
        tags = [{"key": "db.mongodb.collection", "value": "users"}]
        self.assertEqual(_extract_nosql_entities(tags, "mongodb"), ["users"])

    def test_extract_nosql_unknown_system(self):
        tags = [{"key": "db.table", "value": "my_table"}]
        self.assertEqual(_extract_nosql_entities(tags, "redis"), ["my_table"])

    def test_extract_nosql_no_match(self):
        tags = []
        self.assertEqual(_extract_nosql_entities(tags, "mongodb"), [])

    # ---- _detect_db_system ----

    def test_detect_mongodb(self):
        self.assertEqual(_detect_db_system("MONGODB find"), "mongodb")

    def test_detect_postgresql(self):
        self.assertEqual(_detect_db_system("POSTGRES query"), "postgresql")

    def test_detect_mysql(self):
        self.assertEqual(_detect_db_system("MYSQL select"), "mysql")

    def test_detect_default_sql(self):
        self.assertEqual(_detect_db_system("SELECT something"), "sql")

    # ---- _is_db_span ----

    def test_is_db_span_with_db_system_tag(self):
        tags = json.dumps([{"key": "db.system", "value": "postgresql"}])
        row = pd.Series({"tags": tags, "operation_name": ""})
        self.assertTrue(_is_db_span(row))

    def test_is_db_span_with_db_statement_tag(self):
        tags = json.dumps([{"key": "db.statement", "value": "SELECT * FROM orders"}])
        row = pd.Series({"tags": tags, "operation_name": ""})
        self.assertTrue(_is_db_span(row))

    def test_is_db_span_operation_name_heuristic(self):
        row = pd.Series({"tags": "", "operation_name": "SELECT * FROM orders"})
        self.assertTrue(_is_db_span(row))

    def test_is_db_span_not_db(self):
        row = pd.Series({"tags": "", "operation_name": "GET /orders"})
        self.assertFalse(_is_db_span(row))

    # ---- extract_db_operations ----

    def _make_span(self, operation_name, tags=None, service_name="svc", span_id="s1", trace_id="t1"):
        return {
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": None,
            "service_name": service_name,
            "operation_name": operation_name,
            "start_time": 1000,
            "duration": 50,
            "tags": json.dumps(tags) if tags else "",
        }

    def test_extract_db_operations_empty_df(self):
        df = pd.DataFrame()
        result = extract_db_operations(df)
        self.assertTrue(result.empty)
        self.assertListEqual(
            list(result.columns),
            ["trace_id", "span_id", "service_name", "db_system", "db_statement", "tables"],
        )

    def test_extract_db_operations_no_db_spans(self):
        df = pd.DataFrame([self._make_span("GET /orders")])
        result = extract_db_operations(df)
        self.assertTrue(result.empty)

    def test_extract_db_operations_with_statement_tag(self):
        tags = [
            {"key": "db.system", "value": "postgresql"},
            {"key": "db.statement", "value": "SELECT * FROM orders"},
        ]
        df = pd.DataFrame([self._make_span("SELECT", tags)])
        result = extract_db_operations(df)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["db_system"], "postgresql")
        self.assertEqual(result.iloc[0]["db_statement"], "SELECT * FROM orders")
        self.assertEqual(result.iloc[0]["tables"], "orders")

    def test_extract_db_operations_mysql_semantic(self):
        tags = [
            {"key": "db.system", "value": "mysql"},
            {"key": "db.statement", "value": "SELECT * FROM products JOIN categories ON ..."},
        ]
        df = pd.DataFrame([self._make_span("SELECT", tags)])
        result = extract_db_operations(df)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["db_system"], "mysql")
        self.assertIn("products", result.iloc[0]["tables"])
        self.assertIn("categories", result.iloc[0]["tables"])

    def test_extract_db_operations_mongodb_nosql(self):
        tags = [
            {"key": "db.system", "value": "mongodb"},
            {"key": "db.mongodb.collection", "value": "users"},
        ]
        df = pd.DataFrame([self._make_span("MONGODB find", tags)])
        result = extract_db_operations(df)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["db_system"], "mongodb")
        self.assertEqual(result.iloc[0]["tables"], "users")

    def test_extract_db_operations_fallback_statement(self):
        tags = [{"key": "db.system", "value": "sqlite"}]
        df = pd.DataFrame([self._make_span("SELECT * FROM reviews", tags)])
        result = extract_db_operations(df)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["db_statement"], "SELECT * FROM reviews")
        self.assertEqual(result.iloc[0]["tables"], "reviews")

    # ---- save_db_operations_csv ----

    def test_save_db_operations_csv(self):
        df = pd.DataFrame({
            "trace_id": ["t1"],
            "span_id": ["s1"],
            "service_name": ["svc1"],
            "db_system": ["postgresql"],
            "db_statement": ["SELECT 1"],
            "tables": ["orders"],
        })
        path = self.test_dir / "db_ops.csv"
        save_db_operations_csv(df, path)
        self.assertTrue(path.exists())
        loaded = pd.read_csv(path)
        self.assertEqual(len(loaded), 1)

    def test_save_db_operations_csv_creates_dirs(self):
        df = pd.DataFrame({
            "trace_id": ["t1"],
            "span_id": ["s1"],
            "service_name": ["svc1"],
            "db_system": ["postgresql"],
            "db_statement": ["SELECT 1"],
            "tables": ["orders"],
        })
        path = self.test_dir / "sub" / "db_ops.csv"
        save_db_operations_csv(df, path)
        self.assertTrue(path.exists())


class MappingBuilderTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_mapping_"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # ---- _build_span_lookup ----

    def test_build_span_lookup(self):
        df = pd.DataFrame({
            "trace_id": ["t1", "t1"],
            "span_id": ["s1", "s2"],
            "parent_span_id": [None, "s1"],
            "service_name": ["svc1", "svc1"],
        })
        lookup = _build_span_lookup(df)
        self.assertIn(("t1", "s1"), lookup)
        self.assertIn(("t1", "s2"), lookup)
        # pandas converts None to nan in object columns
        self.assertTrue(pd.isna(lookup[("t1", "s1")]["parent_span_id"]))

    # ---- _build_endpoint_lookup ----

    def test_build_endpoint_lookup(self):
        df = pd.DataFrame({
            "trace_id": ["t1"],
            "span_id": ["s1"],
            "endpoint_key": ["GET /orders"],
        })
        lookup = _build_endpoint_lookup(df)
        self.assertEqual(lookup[("t1", "s1")], "GET /orders")

    # ---- _find_endpoint_for_db_span ----

    def test_find_endpoint_direct_match(self):
        span_lookup = {("t1", "s1"): {"parent_span_id": None, "service_name": "svc1"}}
        endpoint_lookup = {("t1", "s1"): "GET /orders"}
        ep, svc = _find_endpoint_for_db_span("t1", "s1", span_lookup, endpoint_lookup)
        self.assertEqual(ep, "GET /orders")
        self.assertEqual(svc, "svc1")

    def test_find_endpoint_walk_parent_chain(self):
        span_lookup = {
            ("t1", "s1"): {"parent_span_id": None, "service_name": "svc1"},
            ("t1", "s2"): {"parent_span_id": "s1", "service_name": "svc1"},
        }
        endpoint_lookup = {("t1", "s1"): "GET /orders"}
        ep, svc = _find_endpoint_for_db_span("t1", "s2", span_lookup, endpoint_lookup)
        self.assertEqual(ep, "GET /orders")
        self.assertEqual(svc, "svc1")

    def test_find_endpoint_no_parent(self):
        span_lookup = {("t1", "s1"): {"parent_span_id": None, "service_name": "svc1"}}
        endpoint_lookup = {}
        ep, svc = _find_endpoint_for_db_span("t1", "s1", span_lookup, endpoint_lookup)
        self.assertIsNone(ep)
        self.assertIsNone(svc)

    def test_find_endpoint_span_not_in_lookup(self):
        ep, svc = _find_endpoint_for_db_span("t1", "unknown", {}, {})
        self.assertIsNone(ep)
        self.assertIsNone(svc)

    # ---- build_endpoint_table_mapping ----

    def test_build_mapping_empty_inputs(self):
        result = build_endpoint_table_mapping(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        self.assertTrue(result.empty)
        self.assertListEqual(list(result.columns), ["service_name", "endpoint_key", "table", "count"])

    def test_build_mapping_normal(self):
        spans_df = pd.DataFrame({
            "trace_id": ["t1", "t1"],
            "span_id": ["s1", "s2"],
            "parent_span_id": [None, "s1"],
            "service_name": ["svc1", "svc1"],
        })
        endpoints_df = pd.DataFrame({
            "trace_id": ["t1"],
            "span_id": ["s1"],
            "service_name": ["svc1"],
            "endpoint_key": ["GET /orders"],
        })
        db_ops_df = pd.DataFrame({
            "trace_id": ["t1"],
            "span_id": ["s2"],
            "tables": ["orders,users"],
        })
        result = build_endpoint_table_mapping(spans_df, endpoints_df, db_ops_df)
        self.assertEqual(len(result), 2)
        self.assertIn("orders", result["table"].values)
        self.assertIn("users", result["table"].values)
        self.assertTrue((result["endpoint_key"] == "GET /orders").all())

    def test_build_mapping_aggregation(self):
        spans_df = pd.DataFrame({
            "trace_id": ["t1", "t1"],
            "span_id": ["s1", "s2"],
            "parent_span_id": [None, "s1"],
            "service_name": ["svc1", "svc1"],
        })
        endpoints_df = pd.DataFrame({
            "trace_id": ["t1"],
            "span_id": ["s1"],
            "service_name": ["svc1"],
            "endpoint_key": ["GET /orders"],
        })
        db_ops_df = pd.DataFrame({
            "trace_id": ["t1", "t1"],
            "span_id": ["s2", "s2"],
            "tables": ["orders", "orders"],
        })
        result = build_endpoint_table_mapping(spans_df, endpoints_df, db_ops_df)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["count"], 2)

    def test_build_mapping_unknown_endpoint_fallback(self):
        # Span whose parent chain has no match in endpoint_lookup
        spans_df = pd.DataFrame({
            "trace_id": ["t1", "t1"],
            "span_id": ["s1", "s2"],
            "parent_span_id": [None, "s1"],
            "service_name": ["svc1", "svc1"],
        })
        endpoints_df = pd.DataFrame({
            "trace_id": ["t1"],
            "span_id": ["s1"],
            "endpoint_key": ["GET /orders"],
            "service_name": ["svc1"],
        })
        db_ops_df = pd.DataFrame({
            "trace_id": ["t1"],
            "span_id": ["s2"],
            "tables": ["orders"],
            "service_name": ["svc1"],
        })
        result = build_endpoint_table_mapping(spans_df, endpoints_df, db_ops_df)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["endpoint_key"], "GET /orders")

    def test_build_mapping_nan_tables_handled(self):
        spans_df = pd.DataFrame({
            "trace_id": ["t1"],
            "span_id": ["s1"],
            "parent_span_id": [None],
            "service_name": ["svc1"],
        })
        endpoints_df = pd.DataFrame({
            "trace_id": ["t1"],
            "span_id": ["s1"],
            "endpoint_key": ["GET /orders"],
            "service_name": ["svc1"],
        })
        db_ops_df = pd.DataFrame({
            "trace_id": ["t1"],
            "span_id": ["s1"],
            "tables": [float("nan")],
        })
        result = build_endpoint_table_mapping(spans_df, endpoints_df, db_ops_df)
        self.assertTrue(result.empty)

    # ---- save_endpoint_table_map_csv ----

    def test_save_endpoint_table_map_csv(self):
        df = pd.DataFrame({
            "service_name": ["svc1"],
            "endpoint_key": ["GET /orders"],
            "table": ["orders"],
            "count": [1],
        })
        path = self.test_dir / "mapping.csv"
        save_endpoint_table_map_csv(df, path)
        self.assertTrue(path.exists())
        loaded = pd.read_csv(path)
        self.assertEqual(len(loaded), 1)

    def test_save_endpoint_table_map_csv_creates_dirs(self):
        df = pd.DataFrame({
            "service_name": ["svc1"],
            "endpoint_key": ["GET /orders"],
            "table": ["orders"],
            "count": [1],
        })
        path = self.test_dir / "sub" / "nested" / "mapping.csv"
        save_endpoint_table_map_csv(df, path)
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
