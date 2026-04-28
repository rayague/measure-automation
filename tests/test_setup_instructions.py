import unittest


class SetupInstructionsTest(unittest.TestCase):
    def test_fastapi_instructions_pass_app(self):
        from boundary_analyzer.auto_setup.setup_instrumentation import INTEGRATION_INSTRUCTIONS

        code = INTEGRATION_INSTRUCTIONS["fastapi"]["code"]
        self.assertIn("init_tracing(app)", code)

    def test_starlette_instructions_pass_app(self):
        from boundary_analyzer.auto_setup.setup_instrumentation import INTEGRATION_INSTRUCTIONS

        code = INTEGRATION_INSTRUCTIONS["starlette"]["code"]
        self.assertIn("init_tracing(app)", code)


if __name__ == "__main__":
    unittest.main()
