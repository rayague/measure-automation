import unittest

from boundary_analyzer.auto.errors import AnalysisError, ErrorCode, unexpected


class AnalysisErrorTest(unittest.TestCase):
    def test_error_code_value(self):
        self.assertEqual(ErrorCode.LANG_NOT_FOUND.value, "discover.lang.NOT_FOUND")

    def test_error_code_uniqueness(self):
        values = [e.value for e in ErrorCode]
        self.assertEqual(len(values), len(set(values)))

    def test_analysis_error_message(self):
        err = AnalysisError(code=ErrorCode.LANG_NOT_FOUND)
        self.assertIn("No supported", err.message)
        self.assertIn("standard build file", str(err))

    def test_analysis_error_with_scope(self):
        err = AnalysisError(code=ErrorCode.LANG_NOT_FOUND, scope="test-project")
        self.assertIn("test-project", err.message)

    def test_analysis_error_with_override(self):
        err = AnalysisError(
            code=ErrorCode.LANG_NOT_FOUND,
            _override_message="Custom message",
            _override_fix="Custom fix",
        )
        self.assertEqual(err.message, "Custom message")
        self.assertEqual(err.fix, "Custom fix")

    def test_analysis_error_code_str(self):
        err = AnalysisError(code=ErrorCode.DOCKER_NOT_FOUND)
        self.assertEqual(err.code_str, "deploy.docker.NOT_FOUND")

    def test_unexpected_error(self):
        original = ValueError("something broke")
        err = unexpected("discover", original)
        self.assertEqual(err.code, ErrorCode.PIPELINE_FAILED)
        self.assertFalse(err.recoverable)
        self.assertIn("ValueError", err.original)

    def test_all_error_codes_have_messages(self):
        for code in ErrorCode:
            err = AnalysisError(code=code)
            self.assertTrue(len(err.message) > 0, f"Missing message for {code.value}")
            self.assertTrue(len(err.detail) > 0, f"Missing detail for {code.value}")
            self.assertIsNotNone(err.fix, f"Missing fix for {code.value}")

    def test_fatal_errors_are_not_recoverable(self):
        fatal_codes = [
            ErrorCode.DOCKER_NOT_FOUND,
            ErrorCode.DOCKER_DAEMON_DOWN,
            ErrorCode.DOCKER_PERMISSION,
            ErrorCode.DOCKER_PORT_CONFLICT,
            ErrorCode.DOCKER_PULL_FAILED,
            ErrorCode.JAEGER_NOT_READY,
            ErrorCode.PIP_NOT_FOUND,
            ErrorCode.ALL_SERVICES_FAILED,
            ErrorCode.LANG_NOT_FOUND,
            ErrorCode.LANG_UNSUPPORTED,
            ErrorCode.PROJECT_EMPTY,
        ]
        for code in fatal_codes:
            err = AnalysisError(code=code)
            self.assertFalse(
                err.recoverable,
                f"{code.value} should be fatal (recoverable=False)",
            )

    def test_non_fatal_errors_are_recoverable(self):
        non_fatal_codes = [
            ErrorCode.OPENAPI_NOT_FOUND,
            ErrorCode.PARTIAL_ENDPOINTS_FAILED,
            ErrorCode.NO_TRACES,
            ErrorCode.PROCESS_KILL_FAILED,
        ]
        for code in non_fatal_codes:
            err = AnalysisError(code=code)
            self.assertTrue(
                err.recoverable,
                f"{code.value} should be recoverable",
            )

    def test_summary_includes_all_parts(self):
        err = AnalysisError(
            code=ErrorCode.LANG_NOT_FOUND,
            scope="test",
            original="File not found",
        )
        summary = err.summary()
        self.assertIn("[discover.lang.NOT_FOUND]", summary)
        self.assertIn("test", summary)
        self.assertIn("standard build file", summary)
