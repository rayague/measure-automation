import os
import tempfile
import unittest
from pathlib import Path

from boundary_analyzer.auto.errors import AnalysisError, ErrorCode
from boundary_analyzer.auto.models import (
    AnalysisReport,
    Endpoint,
    EntryPoint,
    ProjectInfo,
    ServiceInfo,
    StepResult,
    TrafficResult,
)


class EndpointTest(unittest.TestCase):

    def test_key_format(self):
        ep = Endpoint(method="GET", path="/users/{id}")
        self.assertEqual(ep.key(), "GET /users/{id}")

    def test_str(self):
        ep = Endpoint(method="POST", path="/users")
        self.assertEqual(str(ep), "POST /users")


class EntryPointTest(unittest.TestCase):

    def test_str(self):
        ep = EntryPoint(path=Path(tempfile.gettempdir()) / "main.py", framework="fastapi")
        expected = os.path.join(tempfile.gettempdir(), "main.py")
        self.assertEqual(str(ep), expected)


class ServiceInfoTest(unittest.TestCase):

    def setUp(self):
        self.service = ServiceInfo(
            name="my-service",
            language="python",
            framework="fastapi",
            entry_points=[EntryPoint(path=Path("main.py"), framework="fastapi")],
            deployment="direct",
            ports=[8000],
        )

    def test_port_property(self):
        self.assertEqual(self.service.port, 8000)

    def test_port_none(self):
        svc = ServiceInfo(
            name="test", language="python", framework="flask",
            entry_points=[EntryPoint(path=Path("app.py"), framework="flask")],
            deployment="direct",
        )
        self.assertIsNone(svc.port)

    def test_str(self):
        s = str(self.service)
        self.assertIn("my-service", s)
        self.assertIn("python", s)
        self.assertIn("8000", s)


class ProjectInfoTest(unittest.TestCase):

    def setUp(self):
        self.services = [
            ServiceInfo(
                name="svc-a", language="python", framework="fastapi",
                entry_points=[EntryPoint(path=Path("a.py"), framework="fastapi")],
                deployment="direct", ports=[8000],
            ),
            ServiceInfo(
                name="svc-b", language="python", framework="flask",
                entry_points=[EntryPoint(path=Path("b.py"), framework="flask")],
                deployment="direct", ports=[8001],
            ),
        ]
        self.project = ProjectInfo(
            services=self.services,
            root_dir=Path(tempfile.gettempdir()) / "project",
            language="python",
            framework="fastapi",
            has_docker=False,
        )

    def test_is_empty_false(self):
        self.assertFalse(self.project.is_empty)

    def test_is_empty_true(self):
        empty = ProjectInfo(services=[], root_dir=Path(tempfile.gettempdir()))
        self.assertTrue(empty.is_empty)

    def test_single_service_true(self):
        single = ProjectInfo(
            services=[self.services[0]],
            root_dir=Path(tempfile.gettempdir()),
        )
        self.assertTrue(single.single_service)

    def test_single_service_false(self):
        self.assertFalse(self.project.single_service)

    def test_service_by_name_found(self):
        svc = self.project.service_by_name("svc-a")
        self.assertIsNotNone(svc)
        self.assertEqual(svc.port, 8000)

    def test_service_by_name_not_found(self):
        svc = self.project.service_by_name("nonexistent")
        self.assertIsNone(svc)


class TrafficResultTest(unittest.TestCase):

    def test_success_rate_zero(self):
        r = TrafficResult()
        self.assertEqual(r.success_rate, 0.0)

    def test_success_rate_half(self):
        r = TrafficResult(total_requests=100, successful_requests=50)
        self.assertEqual(r.success_rate, 0.5)

    def test_all_succeeded(self):
        r = TrafficResult(
            total_requests=50, successful_requests=50,
            endpoints_tested=5, endpoints_ok=5,
        )
        self.assertTrue(r.all_succeeded)

    def test_none_succeeded(self):
        r = TrafficResult(
            total_requests=50, successful_requests=0,
            endpoints_tested=5, endpoints_ok=0,
        )
        self.assertTrue(r.none_succeeded)

    def test_not_none_succeeded_when_some_ok(self):
        r = TrafficResult(
            total_requests=50, successful_requests=30,
            endpoints_tested=5, endpoints_ok=3,
        )
        self.assertFalse(r.none_succeeded)


class StepResultTest(unittest.TestCase):

    def test_success_no_errors(self):
        sr = StepResult(success=True, step_name="discover")
        self.assertFalse(sr.has_errors)
        self.assertFalse(sr.has_warnings)
        self.assertEqual(sr.status_icon, "v")

    def test_with_errors(self):
        err = AnalysisError(code=ErrorCode.LANG_NOT_FOUND)
        sr = StepResult(success=False, step_name="discover", errors=[err])
        self.assertTrue(sr.has_errors)
        self.assertEqual(sr.status_icon, "X")

    def test_with_warnings(self):
        warn = AnalysisError(code=ErrorCode.OPENAPI_NOT_FOUND)
        sr = StepResult(success=True, step_name="discover", warnings=[warn])
        self.assertTrue(sr.has_warnings)
        self.assertEqual(sr.status_icon, "!")

    def test_merge(self):
        sr1 = StepResult(success=True, step_name="test")
        sr2 = StepResult(success=False, step_name="test",
                          errors=[AnalysisError(code=ErrorCode.LANG_NOT_FOUND)])
        sr1.merge(sr2)
        self.assertFalse(sr1.success)
        self.assertEqual(len(sr1.errors), 1)


class AnalysisReportTest(unittest.TestCase):

    def setUp(self):
        self.report = AnalysisReport(
            project=ProjectInfo(services=[], root_dir=Path(tempfile.gettempdir())),
        )
        self.report.steps["discover"] = StepResult(success=True, step_name="discover")
        self.report.steps["deploy"] = StepResult(success=True, step_name="deploy")

    def test_all_success(self):
        self.assertTrue(self.report.all_success)

    def test_not_all_success(self):
        self.report.steps["traffic"] = StepResult(
            success=False, step_name="traffic",
            errors=[AnalysisError(code=ErrorCode.NO_ENDPOINTS_FOUND)],
        )
        self.assertFalse(self.report.all_success)

    def test_step_not_found(self):
        self.assertIsNone(self.report.step("nonexistent"))

    def test_step_found(self):
        s = self.report.step("discover")
        self.assertIsNotNone(s)
        self.assertTrue(s.success)

    def test_all_errors_empty(self):
        self.assertEqual(len(self.report.all_errors()), 0)

    def test_all_errors_non_empty(self):
        self.report.steps["traffic"] = StepResult(
            success=False, step_name="traffic",
            errors=[AnalysisError(code=ErrorCode.NO_TRACES)],
        )
        self.assertEqual(len(self.report.all_errors()), 1)

    def test_all_warnings_empty(self):
        self.assertEqual(len(self.report.all_warnings()), 0)

    def test_has_any_errors_true(self):
        self.report.steps["traffic"] = StepResult(
            success=False, step_name="traffic",
            errors=[AnalysisError(code=ErrorCode.NO_TRACES)],
        )
        self.assertTrue(self.report.has_any_errors)
