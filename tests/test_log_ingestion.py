"""Tests for the universal log ingestion module (boundary_analyzer.parsing.log_ingestion).

This module is the primary entry point for turning *any* file a user hands us
(Jaeger/Zipkin/OTLP exports, Locust CSVs, nginx/W3C access logs, generic
app logs, JSON Lines, or arbitrary unstructured text) into the canonical
spans DataFrame. It previously had zero direct test coverage.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from boundary_analyzer.parsing.log_ingestion import (
    SPANS_COLUMNS,
    detect_format,
    ingest_log_file,
)


class TempFileMixin:
    def _write(self, name: str, content: str, encoding: str = "utf-8") -> Path:
        tmpdir = Path(tempfile.mkdtemp(prefix="log_ingestion_test_"))
        path = tmpdir / name
        path.write_text(content, encoding=encoding)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmpdir, ignore_errors=True))
        return path


class DetectFormatTest(TempFileMixin, unittest.TestCase):
    def test_detects_jaeger(self):
        content = json.dumps({"data": [{"traceID": "a1", "spans": [{"spanID": "s1", "operationName": "GET /x", "tags": [], "references": []}], "processes": {}}]})
        path = self._write("trace.json", content)
        fmt, conf = detect_format(path)
        self.assertEqual(fmt, "jaeger")
        self.assertGreaterEqual(conf, 0.9)

    def test_detects_otlp(self):
        content = json.dumps({"resourceSpans": [{"resource": {"attributes": []}, "scopeSpans": [{"spans": []}]}]})
        path = self._write("otlp.json", content)
        fmt, _ = detect_format(path)
        self.assertEqual(fmt, "otlp")

    def test_detects_zipkin(self):
        content = json.dumps([{"traceId": "t1", "id": "s1", "name": "get", "localEndpoint": {"serviceName": "orders"}}])
        path = self._write("zipkin.json", content)
        fmt, _ = detect_format(path)
        self.assertEqual(fmt, "zipkin")

    def test_detects_locust_csv(self):
        content = "Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time\nGET,/orders,10,0,20,22\n"
        path = self._write("stats.csv", content)
        fmt, _ = detect_format(path)
        self.assertEqual(fmt, "locust")

    def test_detects_nginx(self):
        content = '127.0.0.1 - - [19/Jun/2026:15:23:01 +0000] "GET /orders HTTP/1.1" 200 512 "-" "curl/8.0"\n'
        path = self._write("access.log", content)
        fmt, _ = detect_format(path)
        self.assertEqual(fmt, "nginx")

    def test_detects_w3c(self):
        content = "#Version: 1.0\n#Date: 2026-06-19 00:00:00\n#Fields: date time cs-method cs-uri-stem sc-status time-taken\n2026-06-19 15:23:01 GET /orders 200 45\n"
        path = self._write("iis.log", content)
        fmt, _ = detect_format(path)
        self.assertEqual(fmt, "w3c")

    def test_detects_json_lines(self):
        lines = [json.dumps({"service": "orders", "method": "GET", "path": "/orders", "timestamp": "2026-06-19T15:23:01Z"}) for _ in range(5)]
        path = self._write("structured.log", "\n".join(lines))
        fmt, _ = detect_format(path)
        self.assertEqual(fmt, "json_lines")

    def test_pure_free_text_does_not_crash_detection(self):
        content = "the quick brown fox jumps over the lazy dog\nthis is just prose, not a log at all\n"
        path = self._write("notes.txt", content)
        fmt, conf = detect_format(path)
        self.assertIsInstance(fmt, str)
        self.assertGreaterEqual(conf, 0.0)


class IngestStructuredFormatsTest(TempFileMixin, unittest.TestCase):
    def test_ingest_jaeger(self):
        content = json.dumps(
            {
                "data": [
                    {
                        "traceID": "a1",
                        "spans": [
                            {"spanID": "s1", "operationName": "GET /orders", "startTime": 1000, "duration": 500, "tags": [{"key": "http.method", "value": "GET"}], "references": [], "processID": "p1"},
                        ],
                        "processes": {"p1": {"serviceName": "orders"}},
                    }
                ]
            }
        )
        path = self._write("trace.json", content)
        result = ingest_log_file(path)
        self.assertEqual(result.format_detected, "jaeger")
        self.assertEqual(len(result.spans_df), 1)
        self.assertEqual(list(result.spans_df.columns), SPANS_COLUMNS)
        self.assertEqual(result.spans_df.iloc[0]["service_name"], "orders")

    def test_ingest_locust_csv(self):
        content = (
            "Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,Requests/s\n"
            "GET,/orders,100,2,20,22.5,5.1\n"
            "Aggregated,,100,2,20,22.5,5.1\n"
        )
        path = self._write("stats.csv", content)
        result = ingest_log_file(path, service_name="orders")
        self.assertEqual(result.format_detected, "locust")
        self.assertEqual(len(result.spans_df), 1)  # aggregated row skipped
        self.assertEqual(result.service_name_used, "orders")

    def test_ingest_generic_sql_correlates_http_and_db(self):
        content = (
            "2026-06-19 15:23:01 INFO django.request: GET /orders/ 200 45ms\n"
            '2026-06-19 15:23:01 DEBUG django.db.backends: (0.012) SELECT "orders_order"."id" FROM "orders_order";\n'
        )
        path = self._write("app.log", content, )
        result = ingest_log_file(path, format_hint="generic_sql")
        self.assertEqual(result.format_detected, "generic_sql")
        self.assertTrue(result.has_db_info)
        self.assertTrue(result.has_trace_correlation)

    def test_ingest_json_lines(self):
        lines = [
            json.dumps({"service": "orders", "method": "GET", "path": "/orders", "timestamp": "2026-06-19T15:23:01Z", "duration_ms": 12}),
            json.dumps({"service": "orders", "sql": "SELECT * FROM orders", "timestamp": "2026-06-19T15:23:01Z"}),
        ]
        path = self._write("structured.jsonl", "\n".join(lines))
        result = ingest_log_file(path, format_hint="json_lines")
        self.assertEqual(len(result.spans_df), 2)
        self.assertTrue(result.has_db_info)

    def test_format_hint_forces_parser(self):
        content = '127.0.0.1 - - [19/Jun/2026:15:23:01 +0000] "GET /orders HTTP/1.1" 200 512\n'
        path = self._write("weird_extension.dat", content)
        result = ingest_log_file(path, format_hint="nginx")
        self.assertEqual(result.format_detected, "nginx")
        self.assertEqual(result.format_confidence, 1.0)


class IngestRawTextFallbackTest(TempFileMixin, unittest.TestCase):
    """The raw_text fallback must guarantee ingestion never hard-fails on a
    file that matches none of the structured formats."""

    def test_pure_prose_never_raises_and_produces_spans(self):
        content = (
            "Startup complete.\n"
            "Listening for connections on port 9000.\n"
            "Cache warmed with 42 entries.\n"
            "Shutting down gracefully.\n"
        )
        path = self._write("weird_custom_format.txt", content)
        result = ingest_log_file(path)  # must not raise
        self.assertEqual(result.format_detected, "raw_text")
        self.assertEqual(len(result.spans_df), 4)
        self.assertLess(result.format_confidence, 0.2)
        self.assertTrue(any("unstructured text" in w for w in result.warnings))

    def test_raw_text_still_extracts_inline_http_and_sql(self):
        # Auto-detection would actually resolve this to generic_sql (which
        # scans every line for the same HTTP/SQL patterns and already
        # produces rows) — that's the *better* outcome and exactly why
        # generic_sql is tried before raw_text in the fallback chain. To
        # exercise raw_text's own best-effort inline extraction, force it
        # explicitly, as a user would via `--format raw_text`.
        content = (
            "some free-form banner text with no timestamps\n"
            "handling GET /orders now\n"
            "running SELECT * FROM orders WHERE id = 1\n"
            "more free-form text\n"
        )
        path = self._write("mixed_custom.txt", content)
        result = ingest_log_file(path, format_hint="raw_text")
        self.assertEqual(result.format_detected, "raw_text")
        self.assertEqual(len(result.spans_df), 4)
        tags_blob = " ".join(result.spans_df["tags"].tolist())
        self.assertIn("http.route", tags_blob)
        self.assertIn("db.statement", tags_blob)

    def test_explicit_raw_text_format_hint(self):
        content = "GET /orders 200\nSELECT * FROM orders\n"
        path = self._write("forced.log", content)
        result = ingest_log_file(path, format_hint="raw_text")
        self.assertEqual(result.format_detected, "raw_text")
        self.assertEqual(result.format_confidence, 1.0)

    def test_log_level_is_captured_as_a_tag(self):
        content = "2026-06-19 12:00:00 ERROR something went wrong in worker 3\n"
        path = self._write("custom.log", content)
        result = ingest_log_file(path)
        self.assertIn("ERROR", result.spans_df.iloc[0]["tags"])


class IngestEdgeCasesTest(TempFileMixin, unittest.TestCase):
    def test_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            ingest_log_file(Path("/nonexistent/path/does_not_exist.log"))

    def test_empty_file_raises_value_error(self):
        path = self._write("empty.log", "   \n\n  ")
        with self.assertRaises(ValueError):
            ingest_log_file(path)

    def test_latin1_encoded_file_is_ingested_via_encoding_fallback(self):
        content = "GET /café 200\n"
        path = self._write("latin1.log", content, encoding="latin-1")
        result = ingest_log_file(path)  # default encoding=utf-8, must fall back
        self.assertGreaterEqual(len(result.spans_df), 1)

    def test_schema_is_always_the_canonical_eight_columns(self):
        path = self._write("app.log", "GET /orders 200\nSELECT * FROM orders\n")
        result = ingest_log_file(path)
        self.assertEqual(list(result.spans_df.columns), SPANS_COLUMNS)
        for col in ("start_time", "duration"):
            self.assertTrue(str(result.spans_df[col].dtype).startswith("int"))


if __name__ == "__main__":
    unittest.main()
