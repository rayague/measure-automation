from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from boundary_analyzer.validation.compare_metrics import (
    compare_scom_methods,
    compare_threshold_methods,
    load_ranking,
    print_scom_comparison,
    print_threshold_comparison,
)


class TestLoadRanking(unittest.TestCase):
    def test_load_existing_csv(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ranking.csv"
            pd.DataFrame({"service_name": ["a"], "scom_score": [0.5], "rank": [1]}).to_csv(path, index=False)
            df = load_ranking(path)
            self.assertEqual(len(df), 1)
            self.assertIn("service_name", df.columns)

    def test_load_nonexistent_file(self):
        with self.assertRaises(FileNotFoundError):
            load_ranking(Path("nonexistent_file.csv"))

    def test_load_empty_csv(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "empty.csv"
            pd.DataFrame({"service_name": []}).to_csv(path, index=False)
            df = load_ranking(path)
            self.assertTrue(df.empty)


class TestCompareScomMethods(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.td_path = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def _write_csv(self, name, data):
        path = self.td_path / name
        pd.DataFrame(data).to_csv(path, index=False)
        return path

    def test_identical_rankings(self):
        data = {
            "service_name": ["a", "b", "c"],
            "scom_score": [0.9, 0.8, 0.7],
            "rank": [1, 2, 3],
        }
        simple = self._write_csv("simple.csv", data)
        weighted = self._write_csv("weighted.csv", data)
        result = compare_scom_methods(simple, weighted)
        self.assertAlmostEqual(result["rank_correlation"], 1.0)

    def test_different_rankings(self):
        simple_data = {
            "service_name": ["a", "b", "c"],
            "scom_score": [0.9, 0.8, 0.7],
            "rank": [1, 2, 3],
        }
        weighted_data = {
            "service_name": ["a", "b", "c"],
            "scom_score": [0.7, 0.9, 0.8],
            "rank": [3, 1, 2],
        }
        simple = self._write_csv("simple2.csv", simple_data)
        weighted = self._write_csv("weighted2.csv", weighted_data)
        result = compare_scom_methods(simple, weighted)
        self.assertIn("rank_correlation", result)
        self.assertIn("significant_rank_changes", result)
        self.assertIn("top_5_simple", result)
        self.assertIn("bottom_5_weighted", result)

    def test_two_rows_identical_ranking(self):
        path = self._write_csv("two_scom.csv", {"service_name": ["a", "b"], "scom_score": [0.8, 0.6], "rank": [1, 2]})
        result = compare_scom_methods(path, path)
        self.assertAlmostEqual(result["rank_correlation"], 1.0)


class TestCompareThresholdMethods(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.td_path = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def _write_csv(self, name, data):
        path = self.td_path / name
        pd.DataFrame(data).to_csv(path, index=False)
        return path

    def test_full_overlap(self):
        data = {
            "service_name": ["a", "b", "c"],
            "is_suspicious": [True, False, True],
            "threshold_value": [0.95, 0.95, 0.95],
        }
        fixed = self._write_csv("fixed.csv", data)
        pct = self._write_csv("pct.csv", data)
        result = compare_threshold_methods(fixed, pct)
        self.assertEqual(result["overlap_count"], 2)
        self.assertEqual(result["only_fixed_count"], 0)
        self.assertEqual(result["only_percentile_count"], 0)

    def test_partial_overlap(self):
        fixed_data = {
            "service_name": ["a", "b", "c"],
            "is_suspicious": [True, False, True],
            "threshold_value": [0.9, 0.9, 0.9],
        }
        pct_data = {
            "service_name": ["a", "b", "c"],
            "is_suspicious": [False, True, True],
            "threshold_value": [0.95, 0.95, 0.95],
        }
        fixed = self._write_csv("fixed2.csv", fixed_data)
        pct = self._write_csv("pct2.csv", pct_data)
        result = compare_threshold_methods(fixed, pct)
        self.assertTrue(result["overlap_count"] >= 1)

    def test_with_zscore(self):
        data = {
            "service_name": ["a", "b"],
            "is_suspicious": [True, False],
            "threshold_value": [0.5, 0.5],
        }
        fixed = self._write_csv("fixed3.csv", data)
        pct = self._write_csv("pct3.csv", data)
        z = self._write_csv("zscore.csv", data)
        result = compare_threshold_methods(fixed, pct, zscore_path=z)
        self.assertIn("zscore_threshold", result)

    def test_comparison_with_nonexistent_file(self):
        with self.assertRaises(FileNotFoundError):
            compare_threshold_methods(Path("nonexistent.csv"), Path("nonexistent.csv"))


class TestPrintFunctions(unittest.TestCase):
    def test_print_scom_comparison(self):
        comparison = {
            "simple_path": "a.csv",
            "weighted_path": "b.csv",
            "rank_correlation": 0.95,
            "rank_correlation_p_value": 0.001,
            "rank_change_stats": {"mean": 1.5, "std": 1.2, "min": 0, "max": 5},
            "scom_change_stats": {"mean": 0.1, "std": 0.05, "min": -0.02, "max": 0.15},
            "significant_rank_changes": [],
            "top_5_simple": ["a", "b"],
            "bottom_5_simple": ["c", "d"],
            "top_5_weighted": ["a", "b"],
            "bottom_5_weighted": ["c", "d"],
        }
        with self.assertLogs("boundary_analyzer.validation.compare_metrics", level=logging.INFO):
            print_scom_comparison(comparison)

    def test_print_threshold_comparison(self):
        comparison = {
            "fixed_path": "a.csv",
            "percentile_path": "b.csv",
            "zscore_path": None,
            "fixed_suspicious_count": 3,
            "percentile_suspicious_count": 2,
            "overlap_count": 1,
            "only_fixed_count": 2,
            "only_percentile_count": 1,
            "overlap_services": [],
            "only_fixed_services": [],
            "only_percentile_services": [],
            "overlap_pct_fixed": 33.33,
            "overlap_pct_percentile": 50.0,
            "fixed_threshold": 0.9,
            "percentile_threshold": 0.95,
            "zscore_threshold": 0.0,
            "zscore_suspicious_count": 0,
            "percentile_zscore_overlap_count": 0,
            "only_zscore_count": 0,
        }
        with self.assertLogs("boundary_analyzer.validation.compare_metrics", level=logging.INFO):
            print_threshold_comparison(comparison)
