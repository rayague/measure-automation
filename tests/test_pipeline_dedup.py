"""Tests that run_pipeline deduplicates spans repeated across input files.

Root cause this guards against: exporting Jaeger traces "per service" returns
the *entire* multi-service trace for every service that participated in it
(see boundary_analyzer.auto.teastore_runner.export_traces), so a request that
touched N services is written to N separate export files. Without dedup by
(trace_id, span_id), concatenating those files inflates endpoint/table
frequency counts and skews the frequency-weighted SCOM score.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from boundary_analyzer.pipeline.run_pipeline import run_pipeline


def _jaeger_trace(trace_id: str, spans: list[dict]) -> dict:
    return {
        "data": [
            {
                "traceID": trace_id,
                "spans": spans,
                "processes": {
                    "p1": {"serviceName": "webui"},
                    "p2": {"serviceName": "persistence"},
                },
            }
        ]
    }


class PipelineDedupTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="dedup_test_"))
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir, ignore_errors=True))
        self.traces_dir = self.tmpdir / "traces"
        self.traces_dir.mkdir()
        self.output_dir = self.tmpdir / "output"

    def _write_shared_trace_across_two_files(self) -> None:
        # A single request: webui (root, HTTP) -> persistence (child, DB).
        # Jaeger's "get traces by service" API returns the FULL trace for
        # *both* services, so exporting per-service writes it to both files.
        shared = _jaeger_trace(
            "trace-1",
            [
                {
                    "spanID": "span-webui",
                    "operationName": "GET /category",
                    "startTime": 1000,
                    "duration": 500,
                    "tags": [{"key": "http.method", "value": "GET"}, {"key": "http.route", "value": "/category"}],
                    "references": [],
                    "processID": "p1",
                },
                {
                    "spanID": "span-persistence",
                    "operationName": "SELECT category",
                    "startTime": 1010,
                    "duration": 100,
                    "tags": [{"key": "db.system", "value": "mysql"}, {"key": "db.statement", "value": "SELECT * FROM category"}],
                    "references": [{"refType": "CHILD_OF", "spanID": "span-webui"}],
                    "processID": "p2",
                },
            ],
        )
        (self.traces_dir / "webui.json").write_text(json.dumps(shared), encoding="utf-8")
        (self.traces_dir / "persistence.json").write_text(json.dumps(shared), encoding="utf-8")

    def test_shared_trace_across_files_is_deduplicated(self):
        self._write_shared_trace_across_two_files()

        rc = run_pipeline(
            traces=self.traces_dir,
            output_dir=self.output_dir,
            scom_method="weighted",
            threshold_method="fixed",
            fixed_threshold=0.5,
        )
        self.assertEqual(rc, 0)

        spans_df = pd.read_csv(self.output_dir / "interim" / "spans.csv")
        # Exactly 2 spans total (webui + persistence), NOT 4, even though the
        # same trace was present verbatim in two separate export files.
        self.assertEqual(len(spans_df), 2)

        summary = json.loads((self.output_dir / "interim" / "ingestion_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["totals"]["duplicate_spans_removed"], 2)
        self.assertEqual(summary["totals"]["total_spans"], 2)

    def test_report_mentions_duplicates_removed(self):
        self._write_shared_trace_across_two_files()

        run_pipeline(
            traces=self.traces_dir,
            output_dir=self.output_dir,
            scom_method="weighted",
            threshold_method="fixed",
            fixed_threshold=0.5,
        )

        report_text = (self.output_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("Duplicate spans removed", report_text)


if __name__ == "__main__":
    unittest.main()
