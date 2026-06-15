from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from boundary_analyzer.cli import _run_pipeline

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces"


class CliPipelineIntegrationTest(unittest.TestCase):
    """End-to-end test of the CLI pipeline runner with fixture traces."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="cli_int_"))
        cls.data_dir = cls.tmpdir / "data"
        cls.traces_dir = cls.data_dir / "raw" / "traces"
        cls.traces_dir.mkdir(parents=True, exist_ok=True)

        for f in FIXTURES_DIR.glob("*.json"):
            shutil.copy2(f, cls.traces_dir / f.name)

        cls.settings_path = cls.tmpdir / "settings.yaml"
        settings = {
            "jaeger_base_url": "http://localhost:16686",
            "service_name": "test",
            "lookback_minutes": 10,
            "limit_traces": 100,
            "output_dir": str(cls.traces_dir),
        }
        with open(cls.settings_path, "w") as f:
            yaml.dump(settings, f)

        os.environ["BOUNDARY_ANALYZER_SETTINGS"] = str(cls.settings_path)
        os.environ["BOUNDARY_ANALYZER_DATA_DIR"] = str(cls.data_dir)
        os.environ["BOUNDARY_ANALYZER_REPORTS_DIR"] = str(cls.data_dir)

        cls.rc = _run_pipeline(skip_collect=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_pipeline_succeeds(self):
        self.assertEqual(self.rc, 0)

    def test_output_files_exist(self):
        self.assertTrue((self.data_dir / "interim" / "spans.csv").exists())
        self.assertTrue((self.data_dir / "interim" / "endpoints.csv").exists())
        self.assertTrue((self.data_dir / "interim" / "db_operations.csv").exists())
        self.assertTrue((self.data_dir / "interim" / "endpoint_table_map.csv").exists())
        self.assertTrue((self.data_dir / "processed" / "service_scom.csv").exists())
        self.assertTrue((self.data_dir / "processed" / "service_rank.csv").exists())
        self.assertTrue((self.data_dir / "processed" / "suspicious_services.csv").exists())
        self.assertTrue((self.data_dir / "latest" / "report.md").exists())

    def test_report_has_content(self):
        report_path = self.data_dir / "latest" / "report.md"
        content = report_path.read_text()
        self.assertIn("auth-service", content)
        self.assertIn("SCOM", content)


if __name__ == "__main__":
    unittest.main()
