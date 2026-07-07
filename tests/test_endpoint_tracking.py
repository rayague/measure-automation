"""Tests for cross-run endpoint tracking (mba endpoint <pattern>)."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from boundary_analyzer.metrics.endpoint_tracking import _endpoint_cohesion, track_endpoint

import pandas as pd


def _write_run(runs_dir: Path, run_id: str, timestamp: str, mapping_rows: list[dict]) -> None:
    run_dir = runs_dir / run_id
    (run_dir / "interim").mkdir(parents=True)
    pd.DataFrame(mapping_rows).to_csv(run_dir / "interim" / "endpoint_table_map.csv", index=False)
    (run_dir / "meta.json").write_text(
        json.dumps({"id": run_id, "timestamp": timestamp, "project_name": "proj"}),
        encoding="utf-8",
    )


class EndpointTrackingTest(unittest.TestCase):
    def setUp(self):
        self.data_root = Path(tempfile.mkdtemp(prefix="eptrack_"))
        self.addCleanup(lambda: shutil.rmtree(self.data_root, ignore_errors=True))
        self.runs_dir = self.data_root / "runs"
        self.runs_dir.mkdir(parents=True)

        # Run 1 (older): GET /orders touches orders+customers; sibling GET /invoices touches orders.
        _write_run(
            self.runs_dir,
            "20260701_000000_proj",
            "2026-07-01T00:00:00",
            [
                {"service_name": "shop", "endpoint_key": "GET /orders", "table": "orders", "count": 10},
                {"service_name": "shop", "endpoint_key": "GET /orders", "table": "customers", "count": 4},
                {"service_name": "shop", "endpoint_key": "GET /invoices", "table": "orders", "count": 6},
            ],
        )
        # Run 2 (newer): /orders now also shares customers with /invoices -> cohesion rises.
        _write_run(
            self.runs_dir,
            "20260702_000000_proj",
            "2026-07-02T00:00:00",
            [
                {"service_name": "shop", "endpoint_key": "GET /orders", "table": "orders", "count": 12},
                {"service_name": "shop", "endpoint_key": "GET /orders", "table": "customers", "count": 5},
                {"service_name": "shop", "endpoint_key": "GET /invoices", "table": "orders", "count": 7},
                {"service_name": "shop", "endpoint_key": "GET /invoices", "table": "customers", "count": 2},
            ],
        )
        # Registry index, newest first is produced by list_runs (reads runs.json order then reverses)
        (self.runs_dir / "runs.json").write_text(
            json.dumps(
                [
                    {"id": "20260701_000000_proj", "timestamp": "2026-07-01T00:00:00"},
                    {"id": "20260702_000000_proj", "timestamp": "2026-07-02T00:00:00"},
                ]
            ),
            encoding="utf-8",
        )

    def test_snapshots_ordered_oldest_to_newest(self):
        snaps = track_endpoint("/orders", data_root=self.data_root)
        self.assertEqual([s.run_id for s in snaps], ["20260701_000000_proj", "20260702_000000_proj"])

    def test_tables_and_counts(self):
        snaps = track_endpoint("GET /orders", data_root=self.data_root)
        self.assertEqual(snaps[0].tables, {"orders": 10, "customers": 4})
        self.assertEqual(snaps[1].total_accesses, 17)

    def test_cohesion_evolution(self):
        snaps = track_endpoint("GET /orders", data_root=self.data_root)
        # Run 1: /invoices has {orders}; overlap = |{orders}| / min(2,1) = 1.0
        self.assertAlmostEqual(snaps[0].cohesion, 1.0)
        # Run 2: /invoices has {orders, customers}; overlap = 2 / min(2,2) = 1.0
        self.assertAlmostEqual(snaps[1].cohesion, 1.0)
        self.assertEqual(snaps[0].sibling_count, 1)

    def test_pattern_is_case_insensitive_substring(self):
        snaps = track_endpoint("get /ORD", data_root=self.data_root)
        self.assertTrue(snaps and all(s.endpoint_key == "GET /orders" for s in snaps))

    def test_service_filter(self):
        snaps = track_endpoint("/orders", service="other-svc", data_root=self.data_root)
        self.assertEqual(snaps, [])

    def test_no_match_returns_empty(self):
        self.assertEqual(track_endpoint("/does-not-exist", data_root=self.data_root), [])


class EndpointCohesionUnitTest(unittest.TestCase):
    def _df(self, rows):
        return pd.DataFrame(rows)

    def test_no_siblings_returns_none(self):
        df = self._df([{"service_name": "s", "endpoint_key": "GET /a", "table": "t1", "count": 1}])
        cohesion, siblings = _endpoint_cohesion("GET /a", df)
        self.assertIsNone(cohesion)
        self.assertEqual(siblings, 0)

    def test_disjoint_siblings_zero(self):
        df = self._df(
            [
                {"service_name": "s", "endpoint_key": "GET /a", "table": "t1", "count": 1},
                {"service_name": "s", "endpoint_key": "GET /b", "table": "t2", "count": 1},
            ]
        )
        cohesion, _ = _endpoint_cohesion("GET /a", df)
        self.assertEqual(cohesion, 0.0)

    def test_partial_overlap(self):
        df = self._df(
            [
                {"service_name": "s", "endpoint_key": "GET /a", "table": "t1", "count": 1},
                {"service_name": "s", "endpoint_key": "GET /a", "table": "t2", "count": 1},
                {"service_name": "s", "endpoint_key": "GET /b", "table": "t1", "count": 1},
                {"service_name": "s", "endpoint_key": "GET /b", "table": "t3", "count": 1},
            ]
        )
        # overlap = |{t1}| / min(2,2) = 0.5
        cohesion, _ = _endpoint_cohesion("GET /a", df)
        self.assertAlmostEqual(cohesion, 0.5)


if __name__ == "__main__":
    unittest.main()
