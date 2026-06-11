from __future__ import annotations

import unittest


class LllmPromptsTest(unittest.TestCase):
    def test_build_instrumentation_prompt_includes_context(self):
        from boundary_analyzer.llm.prompts import build_instrumentation_prompt
        prompt = build_instrumentation_prompt("CONTEXT_DATA_HERE")
        self.assertIn("CONTEXT_DATA_HERE", prompt)
        self.assertIn("OpenTelemetry", prompt)
        self.assertIn("ERROR:", prompt)

    def test_build_instrumentation_prompt_has_system_instructions(self):
        from boundary_analyzer.llm.prompts import INSTRUMENTATION_SYSTEM
        self.assertIn("OTLP gRPC", INSTRUMENTATION_SYSTEM)
        self.assertIn("COMPLETE modified file", INSTRUMENTATION_SYSTEM)
        self.assertIn("VALID PYTHON", INSTRUMENTATION_SYSTEM)

    def test_build_analysis_prompt_includes_data(self):
        from boundary_analyzer.llm.prompts import build_analysis_prompt
        prompt = build_analysis_prompt(
            "service,scom\nsvc1,0.5\nsvc2,0.8",
            "endpoint,table\n/ep1,users\n/ep2,orders",
            "Project context text"
        )
        self.assertIn("svc1,0.5", prompt)
        self.assertIn("svc2,0.8", prompt)
        self.assertIn("/ep1,users", prompt)
        self.assertIn("/ep2,orders", prompt)
        self.assertIn("Project context text", prompt)
        self.assertIn("suspicious", prompt)
        self.assertIn("SCOM", prompt)


if __name__ == "__main__":
    unittest.main()
