import shutil
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from boundary_analyzer.pipeline.run_pipeline import run_pipeline

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "teastore" / "traces"


class TeaStorePipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="teastore_scom_"))
        cls.output_dir = cls.tmpdir / "output"

        cls.rc = run_pipeline(
            traces=FIXTURES_DIR,
            output_dir=cls.output_dir,
            scom_method="weighted",
            threshold_method="fixed",
            fixed_threshold=0.5,
            exclude_services=None,
            exclude_health_routes=True,
            exclude_http_client_spans=True,
            exclude_unknown_endpoint=True,
            skip_no_db_services=False,
        )

        cls.scom = pd.read_csv(cls.output_dir / "processed" / "service_scom.csv")
        cls.endpoints = pd.read_csv(cls.output_dir / "interim" / "endpoints.csv")
        cls.mapping = pd.read_csv(cls.output_dir / "interim" / "endpoint_table_map.csv")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_pipeline_succeeds(self):
        self.assertEqual(self.rc, 0)

    def test_no_ready_endpoints(self):
        for ep in self.endpoints["endpoint_key"]:
            self.assertNotIn("ready", str(ep).lower(), f"Health endpoint found: {ep}")
            self.assertNotIn("isready", str(ep).lower(), f"Health endpoint found: {ep}")

    def test_persistence_service_scom_above_zero(self):
        row = self.scom[self.scom["service_name"] == "persistence-service"]
        self.assertFalse(row.empty, "persistence-service missing from SCOM results")
        self.assertGreater(row.iloc[0]["scom_score"], 0.0)

    def test_auth_service_has_zero_tables(self):
        row = self.scom[self.scom["service_name"] == "auth-service"]
        self.assertFalse(row.empty, "auth-service missing from SCOM results")
        self.assertEqual(row.iloc[0]["tables_count"], 0)

    def test_auth_service_scom_is_zero(self):
        row = self.scom[self.scom["service_name"] == "auth-service"]
        self.assertFalse(row.empty, "auth-service missing from SCOM results")
        self.assertEqual(row.iloc[0]["scom_score"], 0.0)

    def test_has_teastore_tables(self):
        tables = set(self.mapping["table"].unique())
        expected = {"persistencecategory", "persistenceproduct", "persistenceuser", "persistenceorder", "persistenceorderitem"}
        for t in expected:
            self.assertIn(t, tables, f"Table '{t}' missing from endpoint_table_map")

    def test_business_endpoint_count(self):
        svc_endpoints = self.endpoints.groupby("service_name")["endpoint_key"].nunique()
        self.assertGreaterEqual(svc_endpoints.get("persistence-service", 0), 4)
        self.assertGreaterEqual(svc_endpoints.get("auth-service", 0), 1)

    def test_db_operations_have_tables(self):
        db_ops = pd.read_csv(self.output_dir / "interim" / "db_operations.csv")
        non_empty = db_ops[db_ops["tables"].notna() & (db_ops["tables"] != "")]
        self.assertGreater(len(non_empty), 0, "No DB operations with tables extracted")

    def test_mapping_contains_all_business_endpoints(self):
        mapped_eps = set(self.mapping["endpoint_key"].unique())
        expected_endpoints = {
            "GET /tools.descartes.teastore.persistence/categories",
            "POST /tools.descartes.teastore.persistence/orders",
            "GET /tools.descartes.teastore.persistence/products/{id}",
            "POST /tools.descartes.teastore.persistence/users",
            "GET /tools.descartes.teastore.persistence/users/name/{name}",
        }
        for ep in expected_endpoints:
            self.assertIn(ep, mapped_eps, f"Expected endpoint '{ep}' missing from mapping")


class TeaStoreSkipNoDbTest(unittest.TestCase):
    """Verify that skip_no_db_services excludes auth-service from results."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="teastore_skip_"))
        cls.output_dir = cls.tmpdir / "output"

        cls.rc = run_pipeline(
            traces=FIXTURES_DIR,
            output_dir=cls.output_dir,
            scom_method="weighted",
            threshold_method="fixed",
            fixed_threshold=0.5,
            exclude_services=None,
            exclude_health_routes=True,
            exclude_http_client_spans=True,
            exclude_unknown_endpoint=True,
            skip_no_db_services=True,
        )

        cls.scom = pd.read_csv(cls.output_dir / "processed" / "service_scom.csv")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_persistence_still_present(self):
        self.assertIn("persistence-service", self.scom["service_name"].values)

    def test_auth_excluded(self):
        self.assertNotIn("auth-service", self.scom["service_name"].values)

    def test_only_persistence_in_results(self):
        self.assertEqual(len(self.scom), 1)

    def test_pipeline_succeeds(self):
        self.assertEqual(self.rc, 0)


class TeaStoreNoFilterTest(unittest.TestCase):
    """Verify that disabling filters preserves health endpoints and all services."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="teastore_nofilter_"))
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
            skip_no_db_services=False,
        )

        cls.endpoints = pd.read_csv(cls.output_dir / "interim" / "endpoints.csv")
        cls.scom = pd.read_csv(cls.output_dir / "processed" / "service_scom.csv")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_ready_endpoint_present(self):
        ready_found = any("ready" in str(ep).lower() for ep in self.endpoints["endpoint_key"])
        self.assertTrue(ready_found, "ready/isready should be present when health filter is disabled")

    def test_auth_included(self):
        self.assertIn("auth-service", self.scom["service_name"].values)

    def test_pipeline_succeeds(self):
        self.assertEqual(self.rc, 0)
