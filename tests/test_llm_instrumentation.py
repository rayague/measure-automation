from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class LllmInstrumentationTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="llm_instr_test_"))

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_generate_instrumentation_calls_llm(self):
        (self.tmpdir / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
        (self.tmpdir / "requirements.txt").write_text("fastapi\n")

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with patch(
                "boundary_analyzer.llm.instrumentation.call_llm",
                return_value="# instrumented\nfrom fastapi import FastAPI\napp = FastAPI()\n",
            ):
                from boundary_analyzer.llm.instrumentation import (
                    generate_instrumentation,
                )

                result = generate_instrumentation(self.tmpdir)
                self.assertIsNotNone(result)
                self.assertIn("instrumented", result)

    def test_generate_instrumentation_returns_none_on_llm_failure(self):
        (self.tmpdir / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
        (self.tmpdir / "requirements.txt").write_text("fastapi\n")

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with patch(
                "boundary_analyzer.llm.instrumentation.call_llm",
                return_value=None,
            ):
                from boundary_analyzer.llm.instrumentation import (
                    generate_instrumentation,
                )

                result = generate_instrumentation(self.tmpdir)
                self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
