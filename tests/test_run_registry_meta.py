"""Regression tests for run-registry metadata aggregation and data-root resolution.

Two wiring bugs found live (2026-07-06, user's scenario1/2/3 runs):

1. `mba runs show` printed a header (`Endpoints: 6  Tables: 8`) that
   contradicted the per-service SCOM table right below it — the header
   summed AST-discovered endpoint counts (a pre-deployment estimate) and
   per-service table counts (double-counting every table shared between
   services), while the table showed trace-measured values.

2. Every registry function defaulted to the *relative* path ``data/`` —
   runs saved by `mba full` inside project A's folder were invisible to
   `mba dashboard`/`mba runs list` launched from anywhere else.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from boundary_analyzer.auto.run_registry import (
    DATA_DIR_ENV_VAR,
    _build_run_meta,
    resolve_data_root,
)


def _fake_report(scom_records, mapping_df=None, services=()):
    svc_objs = []
    for name, ast_endpoints in services:
        eps = [SimpleNamespace(key=lambda k=e: k) for e in ast_endpoints]
        svc_objs.append(SimpleNamespace(name=name, language="python", framework="flask", endpoints=eps))
    scom_results = {"scom_df": pd.DataFrame(scom_records)}
    if mapping_df is not None:
        scom_results["mapping_df"] = mapping_df
    return SimpleNamespace(
        project=SimpleNamespace(services=svc_objs, root_dir=Path("."), language="python", name="proj"),
        scom_results=scom_results,
        step=lambda _name: None,
        all_errors=lambda: [],
        all_warnings=lambda: [],
        total_duration_seconds=1.0,
        all_success=True,
    )


class HeaderTotalsMatchScomTableTest(unittest.TestCase):
    def test_endpoints_total_sums_trace_measured_counts(self):
        report = _fake_report(
            scom_records=[
                {"service_name": "svc-a", "scom_score": 0.1, "endpoints_count": 10, "tables_count": 4},
                {"service_name": "setup", "scom_score": 0.0, "endpoints_count": 6, "tables_count": 4},
            ],
            # AST discovery found different (stale) counts — must NOT win.
            services=[("svc-a", ["GET /x"]), ("setup", ["GET /init"])],
        )
        meta = _build_run_meta(report, "runid", Path("r.md"), Path("b"))
        self.assertEqual(meta.endpoints_total, 16)  # 10 + 6, not 2 (AST)

    def test_tables_total_is_unique_count_from_mapping(self):
        # Both services touch the SAME 4 tables — total must be 4, not 8.
        mapping = pd.DataFrame(
            {
                "service_name": ["svc-a"] * 4 + ["setup"] * 4,
                "endpoint_key": ["GET /x"] * 4 + ["GET /init"] * 4,
                "table": ["customers", "employees", "products", "orders"] * 2,
                "count": [1] * 8,
            }
        )
        report = _fake_report(
            scom_records=[
                {"service_name": "svc-a", "scom_score": 0.1, "endpoints_count": 5, "tables_count": 4},
                {"service_name": "setup", "scom_score": 0.0, "endpoints_count": 5, "tables_count": 4},
            ],
            mapping_df=mapping,
        )
        meta = _build_run_meta(report, "runid", Path("r.md"), Path("b"))
        self.assertEqual(meta.tables_total, 4)

    def test_tables_total_without_mapping_never_double_counts(self):
        report = _fake_report(
            scom_records=[
                {"service_name": "svc-a", "scom_score": 0.1, "endpoints_count": 5, "tables_count": 4},
                {"service_name": "setup", "scom_score": 0.0, "endpoints_count": 5, "tables_count": 4},
            ],
        )
        meta = _build_run_meta(report, "runid", Path("r.md"), Path("b"))
        # Best non-double-counting bound is max(4, 4) = 4, never 8.
        self.assertEqual(meta.tables_total, 4)

    def test_ast_fallback_when_no_scom_rows(self):
        report = _fake_report(scom_records=[], services=[("svc-a", ["GET /x", "POST /x"])])
        meta = _build_run_meta(report, "runid", Path("r.md"), Path("b"))
        self.assertEqual(meta.endpoints_total, 2)


class ResolveDataRootTest(unittest.TestCase):
    def test_env_var_wins(self):
        with patch.dict(os.environ, {DATA_DIR_ENV_VAR: r"C:\custom\mba-data"}):
            self.assertEqual(resolve_data_root(), Path(r"C:\custom\mba-data"))

    def test_local_registry_wins_when_present(self):
        with patch.dict(os.environ, {DATA_DIR_ENV_VAR: ""}):
            with patch("boundary_analyzer.auto.run_registry.Path.exists", return_value=True):
                self.assertEqual(resolve_data_root(), Path("data"))

    def test_central_dir_when_no_local_registry(self):
        with patch.dict(os.environ, {DATA_DIR_ENV_VAR: ""}):
            with patch("boundary_analyzer.auto.run_registry.Path.exists", return_value=False):
                root = resolve_data_root()
        # Must be absolute (cwd-independent) and namespaced to the tool.
        self.assertTrue(root.is_absolute())
        self.assertIn("boundary_analyzer", str(root))


if __name__ == "__main__":
    unittest.main()
