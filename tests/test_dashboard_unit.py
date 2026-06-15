from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
from dash import html

from boundary_analyzer.dashboard.app import (
    _build_bar_chart,
    _build_distribution,
    _build_heatmap,
    _build_radar_chart,
    _get_data_freshness,
    _load_all,
    _load_endpoint_table_map_from,
    _load_llm_analysis,
    _load_service_rank_from,
)
from boundary_analyzer.dashboard.charts import (
    _base_layout,
    _kde,
    create_summary_cards,
)
from boundary_analyzer.dashboard.design_tokens import _with_alpha


class TestDataLoaders(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.td_path = Path(self.td.name)
        self.processed = self.td_path / "processed"
        self.processed.mkdir()
        self.interim = self.td_path / "interim"
        self.interim.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def test_load_service_rank_from_existing(self):
        data = {"service_name": ["a"], "scom_score": [0.5]}
        pd.DataFrame(data).to_csv(self.processed / "service_rank.csv", index=False)
        df = _load_service_rank_from(self.td_path)
        self.assertEqual(len(df), 1)
        self.assertEqual(df["service_name"].iloc[0], "a")

    def test_load_service_rank_from_missing(self):
        df = _load_service_rank_from(self.td_path)
        self.assertTrue(df.empty)

    def test_load_endpoint_table_map_from_existing(self):
        data = {"service_name": ["a"], "endpoint_key": ["GET /"], "table": ["t1"], "count": [1]}
        pd.DataFrame(data).to_csv(self.interim / "endpoint_table_map.csv", index=False)
        df = _load_endpoint_table_map_from(self.td_path)
        self.assertEqual(len(df), 1)

    def test_load_endpoint_table_map_from_missing(self):
        df = _load_endpoint_table_map_from(self.td_path)
        self.assertTrue(df.empty)

    def test_load_all_empty_dir(self):
        rank_df, mapping_df, summary = _load_all(self.td_path)
        self.assertTrue(rank_df.empty)
        self.assertTrue(mapping_df.empty)

    def test_load_all_no_scom_score_column(self):
        pd.DataFrame({"service_name": ["a"], "scom_score": [0.5], "is_suspicious": [False]}).to_csv(self.processed / "service_rank.csv", index=False)
        pd.DataFrame({"service_name": ["a"], "endpoint_key": ["GET /"], "table": ["t1"], "count": [1]}).to_csv(self.interim / "endpoint_table_map.csv", index=False)
        rank_df, mapping_df, summary = _load_all(self.td_path)
        self.assertFalse(rank_df.empty)
        self.assertEqual(summary["total_services"], 1)

    def test_load_all_with_minimal_data(self):
        pd.DataFrame({
            "service_name": ["a", "b"],
            "scom_score": [0.8, 0.6],
            "is_suspicious": [False, True],
        }).to_csv(self.processed / "service_rank.csv", index=False)
        pd.DataFrame({
            "service_name": ["a"], "endpoint_key": ["GET /"], "table": ["t1"], "count": [1],
        }).to_csv(self.interim / "endpoint_table_map.csv", index=False)
        rank_df, mapping_df, summary = _load_all(self.td_path)
        self.assertEqual(len(rank_df), 2)
        self.assertEqual(len(mapping_df), 1)
        self.assertEqual(summary["suspicious_count"], 1)

    def test_load_all_creates_summary(self):
        rank_data = {
            "service_name": ["a", "b"],
            "scom_score": [0.8, 0.6],
            "endpoints_count": [3, 2],
            "tables_count": [2, 1],
            "is_suspicious": [False, True],
            "rank": [1, 2],
            "threshold_value": [0.5, 0.5],
            "threshold_method": ["percentile", "percentile"],
        }
        pd.DataFrame(rank_data).to_csv(self.processed / "service_rank.csv", index=False)
        mapping_data = {"service_name": ["a"], "endpoint_key": ["GET /"], "table": ["t1"], "count": [1]}
        pd.DataFrame(mapping_data).to_csv(self.interim / "endpoint_table_map.csv", index=False)
        rank_df, mapping_df, summary = _load_all(self.td_path)
        self.assertEqual(len(rank_df), 2)
        self.assertIn("avg_scom", summary)

    def test_get_data_freshness_existing(self):
        pd.DataFrame({"a": [1]}).to_csv(self.processed / "service_rank.csv", index=False)
        freshness = _get_data_freshness(self.td_path)
        self.assertIsInstance(freshness, str)
        self.assertNotEqual(freshness, "unknown")

    def test_get_data_freshness_missing(self):
        freshness = _get_data_freshness(self.td_path)
        self.assertEqual(freshness, "unknown")

    def test_get_data_freshness_from_nonexistent(self):
        with tempfile.TemporaryDirectory() as td:
            freshness = _get_data_freshness(Path(td) / "nosuchdir")
            self.assertEqual(freshness, "unknown")

    def test_load_llm_analysis_with_marker(self):
        reports_dir = Path("reports") / "latest"
        reports_dir.mkdir(parents=True, exist_ok=True)
        content = "Pre text\n## AI-Powered Analysis\nThis is the analysis.\n## Other"
        (reports_dir / "report.md").write_text(content)
        try:
            result = _load_llm_analysis()
            self.assertIsNotNone(result)
            self.assertIn("This is the analysis", result)
        finally:
            (reports_dir / "report.md").unlink(missing_ok=True)

    def test_load_llm_analysis_without_marker(self):
        reports_dir = Path("reports") / "latest"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "report.md").write_text("No analysis here")
        try:
            result = _load_llm_analysis()
            self.assertIsNone(result)
        finally:
            (reports_dir / "report.md").unlink(missing_ok=True)

    def test_load_llm_analysis_missing_file(self):
        result = _load_llm_analysis()
        self.assertIsNone(result)

    def test_load_llm_analysis_with_marker_no_next_h1(self):
        reports_dir = Path("reports") / "latest"
        reports_dir.mkdir(parents=True, exist_ok=True)
        content = "## AI-Powered Analysis\nJust the analysis without further headers."
        (reports_dir / "report.md").write_text(content)
        try:
            result = _load_llm_analysis()
            self.assertIsNotNone(result)
        finally:
            (reports_dir / "report.md").unlink(missing_ok=True)

    def test_load_llm_analysis_with_marker_with_h1(self):
        reports_dir = Path("reports") / "latest"
        reports_dir.mkdir(parents=True, exist_ok=True)
        content = "## AI-Powered Analysis\nSome analysis\n# Next Section\nMore content"
        (reports_dir / "report.md").write_text(content)
        try:
            result = _load_llm_analysis()
            self.assertIsNotNone(result)
            self.assertNotIn("Next Section", result)
        finally:
            (reports_dir / "report.md").unlink(missing_ok=True)

    def test_load_llm_analysis_oserror_path(self):
        reports_dir = Path("reports") / "latest"
        reports_dir.mkdir(parents=True, exist_ok=True)
        dir_entry_path = reports_dir / "report.md"
        dir_entry_path.mkdir(exist_ok=True)
        try:
            result = _load_llm_analysis()
            self.assertIsNone(result)
        finally:
            dir_entry_path.rmdir()

    def test_get_data_freshness_oserror(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            processed = tdp / "processed"
            processed.mkdir(parents=True)
            pd.DataFrame({"a": [1]}).to_csv(processed / "service_rank.csv", index=False)
            with patch.object(Path, "stat", side_effect=OSError):
                freshness = _get_data_freshness(tdp)
            self.assertEqual(freshness, "unknown")

    def test_load_all_resolve_oserror(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            processed = tdp / "processed"
            processed.mkdir(parents=True)
            pd.DataFrame({"service_name": ["a"], "scom_score": [0.5], "is_suspicious": [False]}).to_csv(
                processed / "service_rank.csv", index=False
            )
            with patch.object(Path, "resolve", side_effect=OSError):
                rank_df, mapping_df, summary = _load_all(tdp)
            self.assertFalse(rank_df.empty)

    def test_load_all_scom_score_typeerror(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            processed = tdp / "processed"
            processed.mkdir(parents=True)
            pd.DataFrame({"service_name": ["a"], "scom_score": [0.5], "is_suspicious": [False]}).to_csv(
                processed / "service_rank.csv", index=False
            )
            with (
                patch("boundary_analyzer.dashboard.app.create_summary_cards") as mock_summary,
                patch("boundary_analyzer.dashboard.app.float", side_effect=TypeError("mock")),
                patch("boundary_analyzer.dashboard.app.logger") as mock_logger,
            ):
                mock_summary.return_value = {"avg_scom": 0.5}
                rank_df, mapping_df, summary = _load_all(tdp)
            self.assertEqual(len(rank_df), 1)
            mock_logger.warning.assert_called_once()


class TestMain(unittest.TestCase):
    def test_main_function(self):
        with (
            patch("boundary_analyzer.dashboard.app.create_app") as mock_create,
            patch("boundary_analyzer.dashboard.app.logger"),
        ):
            from boundary_analyzer.dashboard.app import main

            mock_app = MagicMock()
            mock_create.return_value = mock_app
            result = main(data_dir=Path("."))
            self.assertEqual(result, 0)
            mock_app.run.assert_called_once()


class TestChartBuilders(unittest.TestCase):
    def test_build_bar_chart_empty(self):
        df = pd.DataFrame()
        fig = _build_bar_chart(df)
        self.assertIsNotNone(fig)

    def test_build_bar_chart_with_threshold_value(self):
        data = {
            "service_name": ["a", "b", "c"],
            "scom_score": [0.8, 0.5, 0.3],
            "is_suspicious": [False, True, True],
            "rank": [3, 2, 1],
            "endpoints_count": [3, 2, 1],
            "tables_count": [2, 1, 1],
            "threshold_value": [0.6, 0.6, 0.6],
        }
        df = pd.DataFrame(data)
        fig = _build_bar_chart(df)
        self.assertEqual(len(fig.data), 1)

    def test_build_bar_chart_with_threshold_alt(self):
        data = {
            "service_name": ["a", "b"],
            "scom_score": [0.8, 0.5],
            "is_suspicious": [False, True],
            "rank": [2, 1],
            "endpoints_count": [3, 2],
            "tables_count": [2, 1],
            "threshold": [0.6, 0.6],
        }
        df = pd.DataFrame(data)
        fig = _build_bar_chart(df)
        self.assertEqual(len(fig.data), 1)

    def test_build_bar_chart_with_data(self):
        data = {
            "service_name": ["a", "b"],
            "scom_score": [0.8, 0.5],
            "is_suspicious": [False, True],
            "rank": [1, 2],
            "endpoints_count": [3, 2],
            "tables_count": [2, 1],
            "threshold_value": [0.6, 0.6],
            "threshold_method": ["fixed", "fixed"],
        }
        df = pd.DataFrame(data)
        fig = _build_bar_chart(df)
        self.assertEqual(len(fig.data), 1)

    def test_build_distribution_empty(self):
        df = pd.DataFrame()
        fig = _build_distribution(df)
        self.assertIsNotNone(fig)

    def test_build_distribution_only_suspicious(self):
        data = {
            "service_name": ["a", "b"],
            "scom_score": [0.3, 0.4],
            "endpoints_count": [1, 2],
            "tables_count": [1, 1],
            "is_suspicious": [True, True],
        }
        df = pd.DataFrame(data)
        fig = _build_distribution(df)
        self.assertIsNotNone(fig)

    def test_build_distribution_only_healthy(self):
        data = {
            "service_name": ["a", "b"],
            "scom_score": [0.8, 0.9],
            "endpoints_count": [3, 2],
            "tables_count": [2, 1],
            "is_suspicious": [False, False],
        }
        df = pd.DataFrame(data)
        fig = _build_distribution(df)
        self.assertIsNotNone(fig)

    def test_build_distribution_with_data(self):
        data = {
            "service_name": ["a", "b"],
            "scom_score": [0.8, 0.5],
            "endpoints_count": [3, 2],
            "tables_count": [2, 1],
            "is_suspicious": [False, True],
        }
        df = pd.DataFrame(data)
        fig = _build_distribution(df)
        self.assertGreaterEqual(len(fig.data), 0)

    def test_build_radar_chart_suspicious(self):
        row = pd.Series({
            "scom_score": 0.4,
            "endpoints_count": 5,
            "tables_count": 3,
            "rank": 1,
            "is_suspicious": True,
        })
        fig = _build_radar_chart(row)
        self.assertIsNotNone(fig)

    def test_build_radar_chart_healthy(self):
        row = pd.Series({
            "scom_score": 0.9,
            "endpoints_count": 3,
            "tables_count": 2,
            "rank": 4,
            "is_suspicious": False,
        })
        fig = _build_radar_chart(row)
        self.assertIsNotNone(fig)

    def test_build_heatmap_empty(self):
        df = pd.DataFrame({"service_name": [], "endpoint_key": [], "table": [], "count": []})
        fig = _build_heatmap(df, "test-svc")
        self.assertIsNotNone(fig)

    def test_build_heatmap_with_data(self):
        data = {
            "service_name": ["svc"] * 3,
            "endpoint_key": ["GET /a", "GET /b", "GET /a"],
            "table": ["t1", "t1", "t2"],
            "count": [5, 3, 2],
        }
        df = pd.DataFrame(data)
        fig = _build_heatmap(df, "svc")
        self.assertIsNotNone(fig)

    def test_build_heatmap_no_match(self):
        data = {
            "service_name": ["svc"],
            "endpoint_key": ["GET /"],
            "table": ["t1"],
            "count": [1],
        }
        df = pd.DataFrame(data)
        fig = _build_heatmap(df, "other-svc")
        self.assertIsNotNone(fig)

    def test_build_heatmap_multi_row(self):
        data = {
            "service_name": ["svc", "svc"],
            "endpoint_key": ["GET /a", "GET /b"],
            "table": ["t1", "t2"],
            "count": [1, 2],
        }
        df = pd.DataFrame(data)
        fig = _build_heatmap(df, "svc")
        self.assertIsNotNone(fig)


class TestLayoutComponents(unittest.TestCase):
    def test_metric_card(self):
        from boundary_analyzer.dashboard.layout_components import _metric_card
        card = _metric_card("Test", 42, "cyan")
        children = card.children
        self.assertIn("Test", str(children[0].children))
        self.assertIn("42", str(children[1].children))

    def test_status_badge_suspicious(self):
        from boundary_analyzer.dashboard.layout_components import _status_badge
        badge = _status_badge(True)
        self.assertIn("SUSPICIOUS", str(badge.children))

    def test_status_badge_healthy(self):
        from boundary_analyzer.dashboard.layout_components import _status_badge
        badge = _status_badge(False)
        self.assertIn("HEALTHY", str(badge.children))

    def test_build_table_empty(self):
        from boundary_analyzer.dashboard.layout_components import _build_table
        result = _build_table(pd.DataFrame())
        self.assertIsNotNone(result)

    def test_build_table_with_data(self):
        from boundary_analyzer.dashboard.layout_components import _build_table
        data = {
            "service_name": ["a", "b"],
            "rank": [1, 2],
            "scom_score": [0.8, 0.5],
            "endpoints_count": [3, 2],
            "tables_count": [2, 1],
            "is_suspicious": [False, True],
        }
        df = pd.DataFrame(data)
        result = _build_table(df)
        self.assertIsNotNone(result)

    def test_render_inline_plain(self):
        from boundary_analyzer.dashboard.layout_components import _render_inline
        result = _render_inline("hello world")
        self.assertEqual(result, ["hello world"])

    def test_render_inline_bold(self):
        from boundary_analyzer.dashboard.layout_components import _render_inline
        result = _render_inline("hello **world**")
        self.assertEqual(len(result), 2)

    def test_render_inline_code(self):
        from boundary_analyzer.dashboard.layout_components import _render_inline
        result = _render_inline("use `code` here")
        self.assertEqual(len(result), 3)

    def test_definitions_block(self):
        from boundary_analyzer.dashboard.layout_components import _definitions_block
        data = {"service_name": ["a"], "threshold_value": [0.5], "threshold_method": ["percentile"]}
        df = pd.DataFrame(data)
        result = _definitions_block(df)
        self.assertIsNotNone(result)

    def test_definitions_block_no_threshold(self):
        from boundary_analyzer.dashboard.layout_components import _definitions_block
        result = _definitions_block(pd.DataFrame())
        self.assertIsNotNone(result)

    def test_build_data_warning_empty_rank(self):
        from pathlib import Path

        from boundary_analyzer.dashboard.layout_components import _build_data_warning
        result = _build_data_warning(pd.DataFrame(), pd.DataFrame({"a": [1]}), Path("."))
        self.assertIsNotNone(result)

    def test_build_data_warning_empty_mapping(self):
        from pathlib import Path

        from boundary_analyzer.dashboard.layout_components import _build_data_warning
        data = {"service_name": ["a"], "scom_score": [0.5]}
        result = _build_data_warning(pd.DataFrame(data), pd.DataFrame(), Path("."))
        self.assertIsNotNone(result)

    def test_build_data_warning_none(self):
        from pathlib import Path

        from boundary_analyzer.dashboard.layout_components import _build_data_warning
        data = {"service_name": ["a"], "scom_score": [0.5]}
        result = _build_data_warning(pd.DataFrame(data), pd.DataFrame({"a": [1]}), Path("."))
        self.assertIsNone(result)

    def test_card(self):
        from boundary_analyzer.dashboard.layout_components import _card
        result = _card("Title", [html.P("child")])
        children = result.children
        self.assertEqual(children[0].children, "Title")


class TestDesignTokens(unittest.TestCase):
    def test_with_alpha_hex(self):
        result = _with_alpha("#00e5ff", 0.5)
        self.assertEqual(result, "rgba(0,229,255,0.5)")

    def test_with_alpha_rgba(self):
        result = _with_alpha("rgba(0,229,255,0.3)", 0.7)
        self.assertEqual(result, "rgba(0,229,255,0.7)")

    def test_with_alpha_rgb(self):
        result = _with_alpha("rgb(0,229,255)", 0.5)
        self.assertEqual(result, "rgba(0,229,255,0.5)")

    def test_with_alpha_invalid(self):
        result = _with_alpha("invalid", 0.5)
        self.assertEqual(result, "invalid")


class TestCharts(unittest.TestCase):
    def test_base_layout_default(self):
        layout = _base_layout()
        self.assertIsInstance(layout, dict)

    def test_base_layout_with_overrides(self):
        layout = _base_layout(height=500)
        self.assertEqual(layout["height"], 500)

    def test_kde(self):
        import numpy as np

        values = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
        x, y = _kde(values, bw=0.1, n=50)
        self.assertEqual(len(x), 50)
        self.assertEqual(len(y), 50)

    def test_create_summary_cards_all_healthy(self):
        data = {"service_name": ["a", "b"], "scom_score": [0.8, 0.9], "is_suspicious": [False, False]}
        df = pd.DataFrame(data)
        result = create_summary_cards(df)
        self.assertEqual(result["total_services"], 2)
        self.assertEqual(result["suspicious_count"], 0)
        self.assertEqual(result["safe_count"], 2)
        self.assertAlmostEqual(result["avg_scom"], 0.85)

    def test_create_summary_cards_empty(self):
        result = create_summary_cards(pd.DataFrame())
        self.assertEqual(result["total_services"], 0)
        self.assertEqual(result["suspicious_count"], 0)
        self.assertEqual(result["safe_count"], 0)

    def test_kde_custom(self):
        import numpy as np
        values = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7])
        x, y = _kde(values, bw=0.05, n=100)
        self.assertEqual(len(x), 100)
        self.assertEqual(len(y), 100)

    def test_base_layout_empty(self):
        layout = _base_layout()
        self.assertIn("paper_bgcolor", layout)

    def test_base_layout_with_height(self):
        layout = _base_layout(height=600)
        self.assertEqual(layout["height"], 600)

    def test_base_layout_with_margin(self):
        layout = _base_layout(margin=dict(t=10))
        self.assertEqual(layout["margin"]["t"], 10)

    def test_create_scom_distribution(self):
        from boundary_analyzer.dashboard.charts import create_scom_distribution
        data = {
            "service_name": ["a", "b"],
            "scom_score": [0.8, 0.5],
            "is_suspicious": [False, True],
            "endpoints_count": [3, 2],
            "tables_count": [2, 1],
        }
        df = pd.DataFrame(data)
        result = create_scom_distribution(df)
        self.assertIsNotNone(result)

    def test_create_scom_distribution_empty(self):
        from boundary_analyzer.dashboard.charts import create_scom_distribution
        result = create_scom_distribution(pd.DataFrame())
        self.assertIsNotNone(result)

    def test_create_scom_distribution_threshold_value(self):
        from boundary_analyzer.dashboard.charts import create_scom_distribution
        data = {
            "service_name": ["a", "b"],
            "scom_score": [0.8, 0.5],
            "is_suspicious": [False, True],
            "endpoints_count": [3, 2],
            "tables_count": [2, 1],
            "threshold_value": [0.6, 0.6],
        }
        df = pd.DataFrame(data)
        result = create_scom_distribution(df)
        self.assertIsNotNone(result)

    def test_create_scom_distribution_threshold_alt(self):
        from boundary_analyzer.dashboard.charts import create_scom_distribution
        data = {
            "service_name": ["a", "b"],
            "scom_score": [0.8, 0.5],
            "is_suspicious": [False, True],
            "endpoints_count": [3, 2],
            "tables_count": [2, 1],
            "threshold": [0.6, 0.6],
        }
        df = pd.DataFrame(data)
        result = create_scom_distribution(df)
        self.assertIsNotNone(result)

    def test_create_cohesion_gauge(self):
        from boundary_analyzer.dashboard.charts import create_cohesion_gauge
        result = create_cohesion_gauge(0.75, "test-service")
        self.assertIsNotNone(result)

    def test_create_summary_cards_with_threshold(self):
        data = {
            "service_name": ["a", "b"],
            "scom_score": [0.8, 0.5],
            "is_suspicious": [False, True],
            "threshold_value": [0.6, 0.6],
        }
        df = pd.DataFrame(data)
        result = create_summary_cards(df)
        self.assertEqual(result["total_services"], 2)

    def test_create_summary_cards_with_data(self):
        data = {
            "service_name": ["a", "b", "c"],
            "scom_score": [0.9, 0.7, 0.5],
            "is_suspicious": [False, False, True],
        }
        df = pd.DataFrame(data)
        result = create_summary_cards(df)
        self.assertEqual(result["total_services"], 3)
        self.assertEqual(result["suspicious_count"], 1)
        self.assertEqual(result["safe_count"], 2)
        self.assertAlmostEqual(result["avg_scom"], (0.9 + 0.7 + 0.5) / 3)
