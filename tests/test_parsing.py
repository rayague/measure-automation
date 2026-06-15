from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from boundary_analyzer.parsing.trace_reader import (
    _extract_tags_as_json,
    _find_parent_span_id,
    _get_service_name,
    _read_one_trace_file,
    read_all_traces,
    save_spans_csv,
)


class TraceReaderHelpersTest(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None

    # ---- _find_parent_span_id ----

    def test_find_parent_span_id_from_references(self):
        span = {
            "references": [
                {"refType": "CHILD_OF", "spanID": "parent123"},
                {"refType": "FOLLOWS_FROM", "spanID": "other"},
            ]
        }
        self.assertEqual(_find_parent_span_id(span), "parent123")

    def test_find_parent_span_id_no_refs(self):
        span = {}
        self.assertIsNone(_find_parent_span_id(span))

    def test_find_parent_span_id_from_field(self):
        span = {"parentSpanID": "parent456"}
        self.assertEqual(_find_parent_span_id(span), "parent456")

    def test_find_parent_span_id_references_preferred(self):
        span = {
            "references": [{"refType": "CHILD_OF", "spanID": "from_ref"}],
            "parentSpanID": "from_field",
        }
        self.assertEqual(_find_parent_span_id(span), "from_ref")

    # ---- _get_service_name ----

    def test_get_service_name_from_processes(self):
        span = {"processID": "p1"}
        processes = {"p1": {"serviceName": "my-service"}}
        self.assertEqual(_get_service_name(span, processes), "my-service")

    def test_get_service_name_from_span_process(self):
        span = {"process": {"serviceName": "legacy-service"}}
        self.assertEqual(_get_service_name(span, None), "legacy-service")

    def test_get_service_name_empty(self):
        self.assertEqual(_get_service_name({}, {}), "")

    def test_get_service_name_prefers_processes(self):
        span = {"processID": "p1", "process": {"serviceName": "old"}}
        processes = {"p1": {"serviceName": "new"}}
        self.assertEqual(_get_service_name(span, processes), "new")

    # ---- _extract_tags_as_json ----

    def test_extract_tags_as_json_jaeger(self):
        span = {"tags": [{"key": "http.method", "value": "GET"}]}
        expected = json.dumps([{"key": "http.method", "value": "GET"}])
        self.assertEqual(_extract_tags_as_json(span), expected)

    def test_extract_tags_as_json_otel(self):
        span = {"attributes": {"http.method": "GET", "http.route": "/orders"}}
        result = _extract_tags_as_json(span)
        parsed = json.loads(result)
        self.assertIn({"key": "http.method", "value": "GET"}, parsed)
        self.assertIn({"key": "http.route", "value": "/orders"}, parsed)

    def test_extract_tags_as_json_empty(self):
        self.assertEqual(_extract_tags_as_json({}), "")

    def test_extract_tags_as_json_tags_preferred(self):
        span = {
            "tags": [{"key": "http.method", "value": "POST"}],
            "attributes": {"http.method": "GET"},
        }
        expected = json.dumps([{"key": "http.method", "value": "POST"}])
        self.assertEqual(_extract_tags_as_json(span), expected)

    # ---- _read_one_trace_file ----

    def test_read_one_trace_file_valid(self):
        trace_data = {
            "data": [
                {
                    "traceID": "abc123",
                    "spans": [
                        {
                            "spanID": "span1",
                            "operationName": "GET /orders",
                            "startTime": 1000,
                            "duration": 50,
                            "tags": [{"key": "http.method", "value": "GET"}],
                            "references": [{"refType": "CHILD_OF", "spanID": "parent1"}],
                            "processID": "p1",
                        }
                    ],
                    "processes": {
                        "p1": {"serviceName": "my-service"}
                    }
                }
            ]
        }
        tmpdir = Path(tempfile.mkdtemp(prefix="test_read_trace_"))
        try:
            file_path = tmpdir / "trace.json"
            file_path.write_text(json.dumps(trace_data), encoding="utf-8")
            rows = _read_one_trace_file(file_path)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["trace_id"], "abc123")
            self.assertEqual(row["span_id"], "span1")
            self.assertEqual(row["parent_span_id"], "parent1")
            self.assertEqual(row["service_name"], "my-service")
            self.assertEqual(row["operation_name"], "GET /orders")
            self.assertEqual(row["start_time"], 1000)
            self.assertEqual(row["duration"], 50)
            tags = json.loads(row["tags"])
            self.assertEqual(tags, [{"key": "http.method", "value": "GET"}])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_read_one_trace_file_with_jaeger_response_wrapper(self):
        trace_data = {
            "jaeger_response": {
                "data": [
                    {
                        "traceID": "def456",
                        "spans": [
                            {
                                "spanID": "span2",
                                "operationName": "POST /users",
                                "startTime": 2000,
                                "duration": 100,
                                "tags": [],
                            }
                        ],
                        "processes": {},
                    }
                ]
            }
        }
        tmpdir = Path(tempfile.mkdtemp(prefix="test_read_trace_"))
        try:
            file_path = tmpdir / "trace.json"
            file_path.write_text(json.dumps(trace_data), encoding="utf-8")
            rows = _read_one_trace_file(file_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["span_id"], "span2")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_read_one_trace_file_empty_traces(self):
        trace_data = {"data": []}
        tmpdir = Path(tempfile.mkdtemp(prefix="test_read_trace_"))
        try:
            file_path = tmpdir / "trace.json"
            file_path.write_text(json.dumps(trace_data), encoding="utf-8")
            rows = _read_one_trace_file(file_path)
            self.assertEqual(rows, [])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class ReadAllTracesTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_read_all_traces_"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write_trace_file(self, name, trace_data):
        path = self.test_dir / name
        path.write_text(json.dumps(trace_data), encoding="utf-8")
        return path

    def test_read_all_traces_empty_directory(self):
        result = read_all_traces(self.test_dir)
        self.assertTrue(result.empty)
        self.assertListEqual(
            list(result.columns),
            ["trace_id", "span_id", "parent_span_id", "service_name",
             "operation_name", "start_time", "duration", "tags"],
        )

    def test_read_all_traces_single_file(self):
        self._write_trace_file("trace1.json", {
            "data": [
                {
                    "traceID": "t1",
                    "spans": [
                        {
                            "spanID": "s1",
                            "operationName": "GET /orders",
                            "startTime": 1000,
                            "duration": 50,
                            "tags": [{"key": "http.method", "value": "GET"}],
                        }
                    ],
                    "processes": {"p1": {"serviceName": "svc1"}},
                }
            ]
        })
        result = read_all_traces(self.test_dir)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["trace_id"], "t1")
        self.assertEqual(result.iloc[0]["span_id"], "s1")

    def test_read_all_traces_multiple_files(self):
        self._write_trace_file("trace1.json", {
            "data": [
                {
                    "traceID": "t1",
                    "spans": [{"spanID": "s1", "operationName": "GET /orders",
                               "startTime": 1000, "duration": 50, "tags": []}],
                    "processes": {},
                }
            ]
        })
        self._write_trace_file("trace2.json", {
            "data": [
                {
                    "traceID": "t2",
                    "spans": [{"spanID": "s2", "operationName": "POST /users",
                               "startTime": 2000, "duration": 100, "tags": []}],
                    "processes": {},
                }
            ]
        })
        result = read_all_traces(self.test_dir)
        self.assertEqual(len(result), 2)
        self.assertIn("t1", result["trace_id"].values)
        self.assertIn("t2", result["trace_id"].values)

    def test_read_all_traces_ignores_non_json_files(self):
        (self.test_dir / "notes.txt").write_text("hello", encoding="utf-8")
        self._write_trace_file("trace.json", {
            "data": [
                {
                    "traceID": "t1",
                    "spans": [{"spanID": "s1", "operationName": "GET /orders",
                               "startTime": 1000, "duration": 50, "tags": []}],
                    "processes": {},
                }
            ]
        })
        result = read_all_traces(self.test_dir)
        self.assertEqual(len(result), 1)

    def test_read_all_traces_directory_not_found(self):
        missing = self.test_dir / "does_not_exist"
        # glob on a non-existent directory returns empty, so we get an empty DataFrame
        result = read_all_traces(missing)
        self.assertTrue(result.empty)


class SaveSpansCsvTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_save_spans_"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_save_spans_csv(self):
        df = pd.DataFrame({
            "trace_id": ["t1"],
            "span_id": ["s1"],
            "parent_span_id": [None],
            "service_name": ["svc1"],
            "operation_name": ["GET /orders"],
            "start_time": [1000],
            "duration": [50],
            "tags": [""],
        })
        path = self.test_dir / "spans.csv"
        save_spans_csv(df, path)
        self.assertTrue(path.exists())
        loaded = pd.read_csv(path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded.iloc[0]["trace_id"], "t1")

    def test_save_spans_csv_creates_dirs(self):
        df = pd.DataFrame({
            "trace_id": ["t1"],
            "span_id": ["s1"],
            "parent_span_id": [None],
            "service_name": ["svc1"],
            "operation_name": ["GET /orders"],
            "start_time": [1000],
            "duration": [50],
            "tags": [""],
        })
        path = self.test_dir / "sub" / "nested" / "spans.csv"
        save_spans_csv(df, path)
        self.assertTrue(path.exists())

    def test_save_spans_csv_empty_df(self):
        df = pd.DataFrame(columns=[
            "trace_id", "span_id", "parent_span_id",
            "service_name", "operation_name", "start_time", "duration", "tags"
        ])
        path = self.test_dir / "empty_spans.csv"
        save_spans_csv(df, path)
        self.assertTrue(path.exists())
        loaded = pd.read_csv(path)
        self.assertTrue(loaded.empty)


if __name__ == "__main__":
    unittest.main()
