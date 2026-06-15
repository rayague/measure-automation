"""Integration test that starts a real Jaeger instance via Docker.

Requires:
- Docker daemon running
- Port 16686 (Jaeger UI / API) free

Skipped automatically if Docker is unavailable or the port is in use.
Run with: pytest tests/test_jaeger_integration.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import requests

pytest_plugins = []


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _jaeger_running() -> bool:
    try:
        resp = requests.get("http://localhost:16686/api/services", timeout=5)
        return resp.ok
    except (requests.RequestException, OSError):
        return False


class JaegerIntegrationTest(unittest.TestCase):
    """Start a real Jaeger container, verify the API works, then clean up."""

    @classmethod
    def setUpClass(cls):
        if not _docker_available():
            raise unittest.SkipTest("Docker not available")

        if _jaeger_running():
            raise unittest.SkipTest("Jaeger already running on port 16686")

        cls.container_name = "mba_test_jaeger"
        subprocess.run(
            ["docker", "rm", "-f", cls.container_name],
            capture_output=True,
        )

        result = subprocess.run(
            [
                "docker", "run", "-d",
                "--name", cls.container_name,
                "-p", "16686:16686",
                "-p", "4317:4317",
                "jaegertracing/all-in-one:latest",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise unittest.SkipTest(
                f"Failed to start Jaeger container: {result.stderr}"
            )

        cls._wait_for_jaeger()

    @classmethod
    def _wait_for_jaeger(cls, timeout: int = 60, interval: int = 2):
        start = time.time()
        while time.time() - start < timeout:
            if _jaeger_running():
                return
            time.sleep(interval)
        subprocess.run(["docker", "rm", "-f", cls.container_name], capture_output=True)
        raise unittest.SkipTest("Jaeger did not become ready within 60s")

    @classmethod
    def tearDownClass(cls):
        subprocess.run(
            ["docker", "rm", "-f", cls.container_name],
            capture_output=True,
        )

    def test_jaeger_api_services(self):
        resp = requests.get("http://localhost:16686/api/services", timeout=10)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("data", data)

    def test_jaeger_api_traces_empty(self):
        resp = requests.get(
            "http://localhost:16686/api/traces?service=test&limit=5",
            timeout=10,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data.get("data", [])), 0)

    def test_step_01_collect_with_running_jaeger(self):
        from boundary_analyzer.pipeline.step_01_collect_traces import main as collect

        with tempfile.TemporaryDirectory(prefix="jaeger_test_") as tmp:
            os.environ["BOUNDARY_ANALYZER_SETTINGS"] = str(
                Path(tmp) / "settings.yaml"
            )
            settings = {
                "jaeger_base_url": "http://localhost:16686",
                "service_name": "nonexistent-test-service",
                "lookback_minutes": 5,
                "limit_traces": 10,
                "output_dir": str(Path(tmp) / "raw" / "traces"),
            }
            settings_path = Path(tmp) / "settings.yaml"
            with open(settings_path, "w") as f:
                json.dump(settings, f)

            rc = collect()
            self.assertEqual(rc, 0, "step_01 should return 0 even with no traces")


if __name__ == "__main__":
    unittest.main()
