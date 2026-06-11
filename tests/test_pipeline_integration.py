import csv
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from boundary_analyzer.metrics.scom import compute_scom
from boundary_analyzer.detection.endpoint_extractor import extract_endpoints
from boundary_analyzer.pipeline.run_pipeline import run_pipeline

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces"


class PipelineIntegrationTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="scom_test_"))
        cls.output_dir = cls.tmpdir / "output"

        cls.rc = run_pipeline(
            traces=FIXTURES_DIR,
            output_dir=cls.output_dir,
            scom_method="weighted",
            threshold_method="fixed",
            fixed_threshold=0.5,
            exclude_services=["gateway"],
            exclude_health_routes=True,
            exclude_http_client_spans=True,
            exclude_unknown_endpoint=True,
        )

        cls.scom = pd.read_csv(cls.output_dir / "processed" / "service_scom.csv")
        cls.endpoints = pd.read_csv(cls.output_dir / "interim" / "endpoints.csv")
        cls.mapping = pd.read_csv(cls.output_dir / "interim" / "endpoint_table_map.csv")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_pipeline_succeeds(self):
        self.assertEqual(self.rc, 0)

    def test_gateway_excluded(self):
        self.assertNotIn("gateway", self.scom["service_name"].values)

    def test_student_service_scom_above_threshold(self):
        row = self.scom[self.scom["service_name"] == "student-service"]
        self.assertFalse(row.empty, "student-service missing from SCOM results")
        self.assertGreater(row.iloc[0]["scom_score"], 0.1)

    def test_enrollment_service_scom_above_threshold(self):
        row = self.scom[self.scom["service_name"] == "enrollment-service"]
        self.assertFalse(row.empty, "enrollment-service missing from SCOM results")
        self.assertGreater(row.iloc[0]["scom_score"], 0.1)

    def test_classroom_service_scom_above_threshold(self):
        row = self.scom[self.scom["service_name"] == "classroom-service"]
        self.assertFalse(row.empty, "classroom-service missing from SCOM results")
        self.assertGreater(row.iloc[0]["scom_score"], 0.1)

    def test_no_health_endpoints(self):
        for ep in self.endpoints["endpoint_key"]:
            self.assertFalse(
                any(prefix in str(ep) for prefix in ["/health", "/metrics", "/favicon.ico"]),
                f"Health endpoint found: {ep}",
            )

    def test_no_http_client_spans(self):
        for ep in self.endpoints["endpoint_key"]:
            self.assertNotIn("http send", str(ep).lower())
            self.assertNotIn("http receive", str(ep).lower())

    def test_business_endpoint_count_reasonable(self):
        svc_endpoints = self.endpoints.groupby("service_name")["endpoint_key"].nunique()
        for svc in ["auth-service", "classroom-service", "enrollment-service", "student-service"]:
            count = svc_endpoints.get(svc, 0)
            self.assertLessEqual(
                count, 12,
                f"{svc}: {count} endpoints — expected ≤ 12 after filtering",
            )

    def test_endpoint_table_map_has_business_tables(self):
        tables = self.mapping["table"].unique()
        expected_tables = {"users", "students", "classrooms", "classroom_schedules", "enrollments"}
        for t in expected_tables:
            self.assertIn(t, tables, f"Table '{t}' missing from endpoint_table_map")

    def test_no_unknown_endpoint_in_mapping(self):
        self.assertFalse(
            (self.mapping["endpoint_key"] == "unknown_endpoint").any(),
            "unknown_endpoint entries found in endpoint_table_map",
        )

    def test_all_services_have_scom_non_zero(self):
        for _, row in self.scom.iterrows():
            with self.subTest(service=row["service_name"]):
                self.assertGreater(
                    row["scom_score"], 0.0,
                    f"{row['service_name']} has SCOM=0.0",
                )


class PipelineNoFilterTest(unittest.TestCase):
    """Verify that disabling filters includes health endpoints and http spans."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="scom_nofilter_"))
        cls.output_dir = cls.tmpdir / "output"

        cls.rc = run_pipeline(
            traces=FIXTURES_DIR,
            output_dir=cls.output_dir,
            scom_method="weighted",
            threshold_method="fixed",
            fixed_threshold=0.5,
            exclude_services=None,
            exclude_health_routes=False,
            exclude_http_client_spans=False,
            exclude_unknown_endpoint=False,
        )

        cls.endpoints = pd.read_csv(cls.output_dir / "interim" / "endpoints.csv")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_gateway_included_when_not_excluded(self):
        endpoints = self.endpoints[self.endpoints["service_name"] == "gateway"]
        self.assertFalse(endpoints.empty, "gateway should be present when not excluded")

    def test_health_endpoint_present_when_not_excluded(self):
        health_found = any("/health" in str(ep) for ep in self.endpoints["endpoint_key"])
        self.assertTrue(health_found, "Health endpoints should be present when not excluded")

    def test_http_send_present_when_not_excluded(self):
        send_found = any("http send" in str(ep).lower() for ep in self.endpoints["endpoint_key"])
        self.assertTrue(send_found, "http send spans should be present when not excluded")

    def test_endpoint_count_higher_without_filtering(self):
        svc_endpoints = self.endpoints.groupby("service_name")["endpoint_key"].nunique()
        for svc in ["auth-service", "classroom-service", "enrollment-service", "student-service"]:
            count = svc_endpoints.get(svc, 0)
            self.assertGreater(count, 5, f"{svc}: only {count} endpoints without filtering — expected > 5")
