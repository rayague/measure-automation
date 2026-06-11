from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch


class LllmInstrumentationTest(unittest.TestCase):
    def test_generate_instrumentation_calls_llm(self):
        project = Path(".") / "tests" / "fixtures" / "test_project"
        project.mkdir(parents=True, exist_ok=True)
        (project / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n"
        )
        (project / "requirements.txt").write_text("fastapi\n")

        try:
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
                with patch(
                    "boundary_analyzer.llm.instrumentation.call_llm",
                    return_value="# instrumented\nfrom fastapi import FastAPI\napp = FastAPI()\n",
                ):
                    from boundary_analyzer.llm.instrumentation import (
                        generate_instrumentation,
                    )
                    result = generate_instrumentation(project)
                    self.assertIsNotNone(result)
                    self.assertIn("instrumented", result)
        finally:
            import shutil
            shutil.rmtree(project, ignore_errors=True)

    def test_generate_instrumentation_returns_none_on_llm_failure(self):
        project = Path(".") / "tests" / "fixtures" / "test_project2"
        project.mkdir(parents=True, exist_ok=True)
        (project / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
        (project / "requirements.txt").write_text("fastapi\n")

        try:
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
                with patch(
                    "boundary_analyzer.llm.instrumentation.call_llm",
                    return_value=None,
                ):
                    from boundary_analyzer.llm.instrumentation import (
                        generate_instrumentation,
                    )
                    result = generate_instrumentation(project)
                    self.assertIsNone(result)
        finally:
            import shutil
            shutil.rmtree(project, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
