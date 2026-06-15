from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from boundary_analyzer.metrics.cohesion_rules import get_threshold, is_suspicious
from boundary_analyzer.metrics.scom import (
    _build_all_endpoints_by_service,
    _build_endpoint_table_sets,
    _compute_service_scom,
    _get_endpoint_frequencies_by_service,
    compute_scom,
    save_scom_csv,
)
from boundary_analyzer.metrics.threshold_ultimate import (
    apply_threshold,
    compute_fixed_threshold,
    compute_percentile_threshold,
    compute_zscore_threshold,
)


class ScomHelpersTest(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None

    # ---- _build_all_endpoints_by_service ----

    def test_build_all_endpoints_from_mapping(self):
        mapping_df = pd.DataFrame(
            {
                "service_name": ["svc1", "svc1"],
                "endpoint_key": ["GET /orders", "GET /users"],
                "table": ["orders", "users"],
                "count": [1, 1],
            }
        )
        result = _build_all_endpoints_by_service(mapping_df, None)
        self.assertIn("svc1", result)
        self.assertEqual(result["svc1"], {"GET /orders", "GET /users"})

    def test_build_all_endpoints_from_endpoints_df(self):
        mapping_df = pd.DataFrame(columns=["service_name", "endpoint_key", "table", "count"])
        endpoints_df = pd.DataFrame(
            {
                "service_name": ["svc1"],
                "endpoint_key": ["GET /orders"],
                "span_id": ["s1"],
                "trace_id": ["t1"],
            }
        )
        result = _build_all_endpoints_by_service(mapping_df, endpoints_df)
        self.assertIn("svc1", result)
        self.assertEqual(result["svc1"], {"GET /orders"})

    def test_build_all_endpoints_both_sources(self):
        mapping_df = pd.DataFrame(
            {
                "service_name": ["svc1"],
                "endpoint_key": ["GET /orders"],
                "table": ["orders"],
                "count": [1],
            }
        )
        endpoints_df = pd.DataFrame(
            {
                "service_name": ["svc1"],
                "endpoint_key": ["GET /users"],
                "span_id": ["s1"],
                "trace_id": ["t1"],
            }
        )
        result = _build_all_endpoints_by_service(mapping_df, endpoints_df)
        self.assertEqual(result["svc1"], {"GET /orders", "GET /users"})

    def test_build_all_endpoints_empty(self):
        result = _build_all_endpoints_by_service(pd.DataFrame(), None)
        self.assertEqual(result, {})

    # ---- _build_endpoint_table_sets ----

    def test_build_endpoint_table_sets(self):
        mapping_df = pd.DataFrame(
            {
                "service_name": ["svc1", "svc1"],
                "endpoint_key": ["GET /orders", "GET /orders"],
                "table": ["orders", "inventory"],
                "count": [1, 1],
            }
        )
        result = _build_endpoint_table_sets(mapping_df, None)
        self.assertIn("svc1", result)
        self.assertIn("GET /orders", result["svc1"])
        self.assertEqual(result["svc1"]["GET /orders"], {"orders", "inventory"})

    def test_build_endpoint_table_sets_includes_empty_endpoints(self):
        mapping_df = pd.DataFrame(
            {
                "service_name": ["svc1"],
                "endpoint_key": ["GET /orders"],
                "table": ["orders"],
                "count": [1],
            }
        )
        endpoints_df = pd.DataFrame(
            {
                "service_name": ["svc1"],
                "endpoint_key": ["GET /users"],
                "span_id": ["s1"],
                "trace_id": ["t1"],
            }
        )
        result = _build_endpoint_table_sets(mapping_df, endpoints_df)
        self.assertIn("GET /orders", result["svc1"])
        self.assertIn("GET /users", result["svc1"])
        self.assertEqual(result["svc1"]["GET /users"], set())

    # ---- _get_endpoint_frequencies_by_service ----

    def test_endpoint_frequencies_from_endpoints_df(self):
        endpoints_df = pd.DataFrame(
            {
                "service_name": ["svc1", "svc1", "svc1"],
                "endpoint_key": ["GET /orders", "GET /orders", "GET /users"],
                "span_id": ["s1", "s2", "s3"],
                "trace_id": ["t1", "t2", "t3"],
            }
        )
        result = _get_endpoint_frequencies_by_service(endpoints_df, pd.DataFrame())
        self.assertIn("svc1", result)
        self.assertAlmostEqual(result["svc1"]["GET /orders"], 2.0 / 3.0)
        self.assertAlmostEqual(result["svc1"]["GET /users"], 1.0 / 3.0)

    def test_endpoint_frequencies_from_mapping_fallback(self):
        mapping_df = pd.DataFrame(
            {
                "service_name": ["svc1", "svc1"],
                "endpoint_key": ["GET /orders", "GET /users"],
                "table": ["orders", "users"],
                "count": [3, 1],
            }
        )
        result = _get_endpoint_frequencies_by_service(None, mapping_df)
        self.assertIn("svc1", result)
        self.assertAlmostEqual(result["svc1"]["GET /orders"], 0.75)
        self.assertAlmostEqual(result["svc1"]["GET /users"], 0.25)

    def test_endpoint_frequencies_empty(self):
        result = _get_endpoint_frequencies_by_service(None, pd.DataFrame())
        self.assertEqual(result, {})

    # ---- _compute_service_scom ----

    def test_compute_scom_fewer_than_two_endpoints(self):
        endpoint_sets = {"GET /orders": {"orders"}}
        self.assertEqual(_compute_service_scom(endpoint_sets, {}, False), 0.0)

    def test_compute_scom_no_overlap(self):
        endpoint_sets = {
            "GET /orders": {"orders"},
            "GET /users": {"users"},
        }
        self.assertEqual(_compute_service_scom(endpoint_sets, {}, False), 0.0)

    def test_compute_scom_partial_overlap(self):
        endpoint_sets = {
            "GET /orders": {"orders", "inventory"},
            "GET /users": {"users", "orders"},
        }
        score = _compute_service_scom(endpoint_sets, {}, False)
        # CI(e1,e2) = |{orders}| = 1
        # CI_max = min(2,2) = 2
        # N = 1 pair
        # SCOM = 1 / (1 * 2) = 0.5
        self.assertAlmostEqual(score, 0.5)

    def test_compute_scom_weighted(self):
        endpoint_sets = {
            "GET /orders": {"orders"},
            "GET /users": {"users", "orders"},
        }
        frequencies = {"GET /orders": 0.6, "GET /users": 0.4}
        score = _compute_service_scom(endpoint_sets, frequencies, True)
        # CI = 1 (intersection of {orders} and {orders, users})
        # w = 0.6 * 0.4 = 0.24
        # CI_max = min(1,2) = 1
        # SCOM = (0.24 * 1) / (0.24 * 1) = 1.0
        self.assertAlmostEqual(score, 1.0)

    def test_compute_scom_ci_max_zero(self):
        endpoint_sets = {
            "GET /orders": set(),
            "GET /users": set(),
        }
        self.assertEqual(_compute_service_scom(endpoint_sets, {}, False), 0.0)


class ComputeScomTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_scom_"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_compute_scom_empty_inputs(self):
        result = compute_scom(pd.DataFrame())
        self.assertTrue(result.empty)
        self.assertListEqual(
            list(result.columns),
            ["service_name", "scom_score", "endpoints_count", "tables_count", "method"],
        )

    def test_compute_scom_single_service_no_overlap(self):
        mapping_df = pd.DataFrame(
            {
                "service_name": ["svc1", "svc1"],
                "endpoint_key": ["GET /orders", "GET /users"],
                "table": ["orders", "users"],
                "count": [1, 1],
            }
        )
        result = compute_scom(mapping_df, use_endpoint_weighting=False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["service_name"], "svc1")
        self.assertAlmostEqual(result.iloc[0]["scom_score"], 0.0)
        self.assertEqual(result.iloc[0]["endpoints_count"], 2)
        self.assertEqual(result.iloc[0]["tables_count"], 2)

    def test_compute_scom_single_service_with_overlap(self):
        mapping_df = pd.DataFrame(
            {
                "service_name": ["svc1", "svc1", "svc1"],
                "endpoint_key": ["GET /orders", "GET /orders", "GET /users"],
                "table": ["orders", "inventory", "orders"],
                "count": [1, 1, 1],
            }
        )
        result = compute_scom(mapping_df, use_endpoint_weighting=False)
        self.assertEqual(len(result), 1)
        # GET /orders -> {orders, inventory}, GET /users -> {orders}
        # CI = 1, CI_max = min(2, 1) = 1, N=1 => 1/(1*1) = 1.0
        self.assertAlmostEqual(result.iloc[0]["scom_score"], 1.0)

    def test_compute_scom_exclude_services(self):
        mapping_df = pd.DataFrame(
            {
                "service_name": ["svc1", "svc2"],
                "endpoint_key": ["GET /orders", "GET /items"],
                "table": ["orders", "items"],
                "count": [1, 1],
            }
        )
        result = compute_scom(mapping_df, exclude_services=["svc1"], use_endpoint_weighting=False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["service_name"], "svc2")

    def test_compute_scom_exclude_unknown_endpoint(self):
        mapping_df = pd.DataFrame(
            {
                "service_name": ["svc1", "svc1"],
                "endpoint_key": ["unknown_endpoint", "GET /orders"],
                "table": ["orders", "orders"],
                "count": [1, 1],
            }
        )
        result = compute_scom(mapping_df, exclude_unknown_endpoint=True, use_endpoint_weighting=False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["service_name"], "svc1")
        self.assertEqual(result.iloc[0]["scom_score"], 0.0)

    def test_compute_scom_skip_no_db_services(self):
        # svc1 has a real table, svc3 has no endpoint->table entries in mapping
        mapping_df = pd.DataFrame(
            {
                "service_name": ["svc1", "svc3"],
                "endpoint_key": ["GET /orders", "GET /items"],
                "table": ["orders", None],
                "count": [1, 1],
            }
        )
        endpoints_df = pd.DataFrame(
            {
                "service_name": ["svc1", "svc3"],
                "endpoint_key": ["GET /orders", "GET /items"],
                "span_id": ["s1", "s2"],
                "trace_id": ["t1", "t2"],
            }
        )
        result = compute_scom(mapping_df, endpoints_df, skip_no_db_services=True, use_endpoint_weighting=False)
        svcs = result["service_name"].tolist()
        self.assertIn("svc1", svcs)
        self.assertNotIn("svc3", svcs)

    def test_compute_scom_weighted_vs_unweighted(self):
        mapping_df = pd.DataFrame(
            {
                "service_name": ["svc1", "svc1"],
                "endpoint_key": ["GET /orders", "GET /users"],
                "table": ["orders", "orders"],
                "count": [1, 1],
            }
        )
        endpoints_df = pd.DataFrame(
            {
                "service_name": ["svc1", "svc1", "svc1"],
                "endpoint_key": ["GET /orders", "GET /orders", "GET /users"],
                "span_id": ["s1", "s2", "s3"],
                "trace_id": ["t1", "t2", "t3"],
            }
        )
        weighted = compute_scom(mapping_df, endpoints_df, use_endpoint_weighting=True)
        unweighted = compute_scom(mapping_df, endpoints_df, use_endpoint_weighting=False)
        self.assertEqual(weighted.iloc[0]["method"], "weighted")
        self.assertEqual(unweighted.iloc[0]["method"], "unweighted")

    # ---- save_scom_csv ----

    def test_save_scom_csv(self):
        df = pd.DataFrame(
            {
                "service_name": ["svc1"],
                "scom_score": [0.5],
                "endpoints_count": [2],
                "tables_count": [3],
                "method": ["unweighted"],
            }
        )
        path = self.test_dir / "scom.csv"
        save_scom_csv(df, path)
        self.assertTrue(path.exists())
        loaded = pd.read_csv(path)
        self.assertEqual(len(loaded), 1)

    def test_save_scom_csv_creates_dirs(self):
        df = pd.DataFrame(
            {
                "service_name": ["svc1"],
                "scom_score": [0.5],
                "endpoints_count": [2],
                "tables_count": [3],
                "method": ["unweighted"],
            }
        )
        path = self.test_dir / "sub" / "nested" / "scom.csv"
        save_scom_csv(df, path)
        self.assertTrue(path.exists())


class CohesionRulesTest(unittest.TestCase):
    def test_get_threshold_default(self):
        self.assertEqual(get_threshold(), 0.5)

    def test_get_threshold_with_settings(self):
        self.assertEqual(get_threshold({"scom_threshold": 0.3}), 0.3)

    def test_get_threshold_settings_without_key(self):
        self.assertEqual(get_threshold({"other_key": 1}), 0.5)

    def test_get_threshold_none_settings(self):
        self.assertEqual(get_threshold(None), 0.5)

    def test_is_suspicious_below_threshold(self):
        self.assertTrue(is_suspicious(0.3, 0.5))

    def test_is_suspicious_at_threshold(self):
        self.assertFalse(is_suspicious(0.5, 0.5))

    def test_is_suspicious_above_threshold(self):
        self.assertFalse(is_suspicious(0.7, 0.5))

    def test_is_suspicious_zero_score(self):
        self.assertTrue(is_suspicious(0.0, 0.1))


class ThresholdUltimateTest(unittest.TestCase):
    # ---- compute_percentile_threshold ----

    def test_percentile_threshold_normal(self):
        scores = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5])
        self.assertAlmostEqual(compute_percentile_threshold(scores, 25.0), 0.2)

    def test_percentile_threshold_empty(self):
        self.assertEqual(compute_percentile_threshold(pd.Series([], dtype=float)), 0.0)

    def test_percentile_threshold_custom(self):
        scores = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5])
        self.assertAlmostEqual(compute_percentile_threshold(scores, 50.0), 0.3)

    # ---- compute_zscore_threshold ----

    def test_zscore_threshold_normal(self):
        scores = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5])
        threshold = compute_zscore_threshold(scores, -1.5)
        mean = scores.mean()
        std = scores.std()
        expected = mean + (-1.5 * std)
        self.assertAlmostEqual(threshold, expected)

    def test_zscore_threshold_empty(self):
        self.assertEqual(compute_zscore_threshold(pd.Series([], dtype=float)), 0.0)

    def test_zscore_threshold_zero_std(self):
        scores = pd.Series([0.5, 0.5, 0.5])
        self.assertEqual(compute_zscore_threshold(scores), 0.0)

    def test_zscore_threshold_custom_z(self):
        scores = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5])
        threshold = compute_zscore_threshold(scores, -2.0)
        mean = scores.mean()
        std = scores.std()
        expected = mean + (-2.0 * std)
        self.assertAlmostEqual(threshold, expected)

    # ---- compute_fixed_threshold ----

    def test_fixed_threshold_default(self):
        scores = pd.Series([0.1, 0.2, 0.3])
        self.assertEqual(compute_fixed_threshold(scores), 0.5)

    def test_fixed_threshold_custom(self):
        scores = pd.Series([0.1, 0.2, 0.3])
        self.assertEqual(compute_fixed_threshold(scores, 0.7), 0.7)

    def test_fixed_threshold_empty(self):
        self.assertEqual(compute_fixed_threshold(pd.Series([], dtype=float)), 0.5)

    # ---- apply_threshold ----

    def test_apply_threshold_percentile_method(self):
        df = pd.DataFrame({"scom_score": [0.1, 0.2, 0.3, 0.4, 0.5]})
        result = apply_threshold(df, threshold_method="percentile", threshold_percentile=25.0)
        self.assertIn("threshold_value", result.columns)
        self.assertIn("threshold_method", result.columns)
        self.assertIn("is_suspicious", result.columns)
        self.assertEqual(result["threshold_method"].iloc[0], "percentile")

    def test_apply_threshold_zscore_method(self):
        df = pd.DataFrame({"scom_score": [0.1, 0.2, 0.3, 0.4, 0.5]})
        result = apply_threshold(df, threshold_method="zscore", threshold_zscore=-1.5)
        self.assertEqual(result["threshold_method"].iloc[0], "zscore")

    def test_apply_threshold_fixed_method(self):
        df = pd.DataFrame({"scom_score": [0.1, 0.2, 0.3, 0.4, 0.5]})
        result = apply_threshold(df, threshold_method="fixed", fixed_threshold=0.35)
        self.assertEqual(result["threshold_method"].iloc[0], "fixed")
        self.assertAlmostEqual(result["threshold_value"].iloc[0], 0.35)
        # Scores below 0.35 are suspicious
        self.assertTrue(result.loc[0, "is_suspicious"])
        self.assertTrue(result.loc[1, "is_suspicious"])
        self.assertTrue(result.loc[2, "is_suspicious"])
        self.assertFalse(result.loc[3, "is_suspicious"])
        self.assertFalse(result.loc[4, "is_suspicious"])

    def test_apply_threshold_empty_df(self):
        df = pd.DataFrame({"scom_score": []})
        result = apply_threshold(df)
        self.assertTrue(result.empty)
        # empty df should still have the new columns
        self.assertIn("threshold_value", result.columns)
        self.assertIn("threshold_method", result.columns)
        self.assertIn("is_suspicious", result.columns)

    def test_apply_threshold_unknown_method(self):
        df = pd.DataFrame({"scom_score": [0.1, 0.2]})
        with self.assertRaises(ValueError):
            apply_threshold(df, threshold_method="invalid")


if __name__ == "__main__":
    unittest.main()
