from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class LllmAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.rank_path = self.tmpdir / "service_rank.csv"
        self.mapping_path = self.tmpdir / "endpoint_table_map.csv"
        self.interim = self.tmpdir / "interim"
        self.interim.mkdir(exist_ok=True)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_none_if_rank_file_missing(self):
        from boundary_analyzer.llm.analysis import generate_narrative_analysis

        result = generate_narrative_analysis(self.rank_path, self.mapping_path)
        self.assertIsNone(result)

    def test_returns_none_if_mapping_file_missing(self):
        self.rank_path.write_text("service,score\nsvc1,0.5\n")
        from boundary_analyzer.llm.analysis import generate_narrative_analysis

        result = generate_narrative_analysis(self.rank_path, self.mapping_path)
        self.assertIsNone(result)

    def test_returns_none_if_rank_empty(self):
        self.rank_path.write_text("service,score\n")
        self.mapping_path.write_text("endpoint,table\n/ep1,users\n")
        from boundary_analyzer.llm.analysis import generate_narrative_analysis

        result = generate_narrative_analysis(self.rank_path, self.mapping_path)
        self.assertIsNone(result)

    def test_calls_llm_with_data(self):
        self.rank_path.write_text("service,score,is_suspicious\nsvc1,0.3,True\nsvc2,0.8,False\n")
        self.mapping_path.write_text("endpoint,table\n/ep1,users\n/ep2,orders\n")

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with patch(
                "boundary_analyzer.llm.analysis.call_llm",
                return_value="## Analysis\nSuspicious: svc1",
            ):
                from boundary_analyzer.llm.analysis import (
                    generate_narrative_analysis,
                )

                result = generate_narrative_analysis(self.rank_path, self.mapping_path, data_dir=self.tmpdir)
                self.assertIsNotNone(result)
                self.assertIn("Analysis", result)

    def test_local_fallback_when_llm_fails(self):
        self.rank_path.write_text("service,score,is_suspicious\nsvc1,0.3,True\n")
        self.mapping_path.write_text("endpoint,table\n/ep1,users\n")

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with patch(
                "boundary_analyzer.llm.analysis.call_llm",
                return_value=None,
            ):
                from boundary_analyzer.llm.analysis import (
                    generate_narrative_analysis,
                )

                result = generate_narrative_analysis(self.rank_path, self.mapping_path)
                self.assertIsNotNone(result)
                self.assertIn("Local computed", result)
                self.assertIn("svc1", result)
                self.assertIn("0.3000", result)

    def test_find_project_context_from_mapping(self):
        (self.interim / "endpoint_table_map.csv").write_text("service_name,endpoint,table\nsvc1,/ep1,users\nsvc2,/ep2,orders\n")
        from boundary_analyzer.llm.analysis import _find_project_context

        result = _find_project_context(self.tmpdir)
        self.assertIn("svc1", result)
        self.assertIn("svc2", result)

    def test_find_project_context_from_endpoints(self):
        (self.interim / "endpoints.csv").write_text("service_name,endpoint_key\nsvc1,/api/v1/items\nsvc2,/api/v1/users\n")
        from boundary_analyzer.llm.analysis import _find_project_context

        result = _find_project_context(self.tmpdir)
        self.assertIn("svc1", result)
        self.assertIn("/api/v1/items", result)

    def test_find_project_context_fallback(self):
        from boundary_analyzer.llm.analysis import _find_project_context

        result = _find_project_context(self.tmpdir)
        self.assertEqual(result, "Project context not available.")


if __name__ == "__main__":
    unittest.main()
