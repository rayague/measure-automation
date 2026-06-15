import tempfile
import unittest
from pathlib import Path

from boundary_analyzer.auto.plugins.python import PythonPlugin


class PythonPluginTest(unittest.TestCase):

    def setUp(self):
        self.plugin = PythonPlugin()
        self.tmpdir = Path(tempfile.mkdtemp(prefix="py_plugin_test_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, path: str, content: str) -> Path:
        full = self.tmpdir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return full

    def test_name(self):
        self.assertEqual(self.plugin.name, "python")

    def test_detect_no_python_files(self):
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.0)

    def test_detect_with_py_file_but_no_build_file(self):
        self._write("hello.py", "x = 1")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.3)
        self.assertEqual(result.framework, "python")

    def test_detect_with_requirements(self):
        self._write("requirements.txt", "flask==2.0")
        self._write("app.py", "from flask import Flask\napp = Flask(__name__)")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.9)
        self.assertEqual(result.framework, "flask")
        self.assertEqual(len(result.entries), 1)

    def test_detect_fastapi(self):
        self._write("requirements.txt", "fastapi==0.100")
        self._write("main.py", "from fastapi import FastAPI\napp = FastAPI()")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "fastapi")
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.entries[0].path.name, "main.py")

    def test_detect_django(self):
        self._write("requirements.txt", "django==4.2")
        self._write("manage.py", "#!/usr/bin/env python\nimport django")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "django")
        self.assertEqual(len(result.entries), 1)

    def test_find_entry_points_common_names(self):
        self._write("requirements.txt", "fastapi")
        self._write("main.py", "from fastapi import FastAPI\napp = FastAPI()")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].path.name, "main.py")

    def test_find_entry_points_main_block(self):
        self._write("requirements.txt", "flask")
        self._write("server.py", "if __name__ == '__main__':\n    pass")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].path.name, "server.py")

    def test_find_entry_points_no_main_block(self):
        self._write("no_main.py", "x = 1\ny = 2")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 0)

    def test_detect_framework_in_file_fastapi(self):
        f = self._write("test.py", "from fastapi import FastAPI\napp = FastAPI()")
        fw = self.plugin._detect_framework_in_file(f)
        self.assertEqual(fw, "fastapi")

    def test_detect_framework_in_file_flask(self):
        f = self._write("test.py", "from flask import Flask\napp = Flask(__name__)")
        fw = self.plugin._detect_framework_in_file(f)
        self.assertEqual(fw, "flask")

    def test_detect_framework_in_file_unknown(self):
        f = self._write("test.py", "x = 1")
        fw = self.plugin._detect_framework_in_file(f)
        self.assertEqual(fw, "python")

    def test_instrument(self):
        entry = self.plugin.find_entry_points(self.tmpdir)
        ep = entry[0] if entry else None
        if not ep:
            self._write("main.py", "x = 1")
            entry = self.plugin.find_entry_points(self.tmpdir)
            ep = entry[0]
        instr = self.plugin.instrument(ep, "my-service")
        self.assertEqual(instr.env_vars["OTEL_SERVICE_NAME"], "my-service")
        self.assertIn("OTEL_EXPORTER_OTLP_ENDPOINT", instr.env_vars)
        self.assertIn("opentelemetry-sdk", instr.files_to_install)

    def test_guess_port_fastapi(self):
        self._write("main.py", "from fastapi import FastAPI\napp = FastAPI()")
        entry = self.plugin.find_entry_points(self.tmpdir)
        self.assertTrue(entry, f"No entry points found in {self.tmpdir}")
        port = self.plugin.guess_port(entry[0])
        self.assertEqual(port, 8000)

    def test_guess_port_flask(self):
        self._write("app.py", "from flask import Flask\napp = Flask(__name__)")
        entry = self.plugin.find_entry_points(self.tmpdir)
        self.assertTrue(entry, f"No entry points found in {self.tmpdir}")
        port = self.plugin.guess_port(entry[0])
        self.assertEqual(port, 5000)

    def test_guess_port_from_env(self):
        self._write("main.py", "x = 1")
        self._write(".env", "PORT=3030")
        entry = self.plugin.find_entry_points(self.tmpdir)
        self.assertTrue(entry, f"No entry points found in {self.tmpdir}")
        port = self.plugin.guess_port(entry[0])
        self.assertEqual(port, 3030)

    def test_run_command_fastapi(self):
        self._write("main.py", "from fastapi import FastAPI\napp = FastAPI()")
        entry = self.plugin.find_entry_points(self.tmpdir)
        self.assertTrue(entry, f"No entry points found in {self.tmpdir}")
        cmd = self.plugin.run_command(entry[0], port=8080)
        self.assertIn("uvicorn", cmd)
        self.assertIn("8080", " ".join(cmd))

    def test_run_command_flask(self):
        self._write("app.py", "from flask import Flask\napp = Flask(__name__)")
        entry = self.plugin.find_entry_points(self.tmpdir)
        self.assertTrue(entry, f"No entry points found in {self.tmpdir}")
        cmd = self.plugin.run_command(entry[0], port=5000)
        self.assertIn("flask", cmd)

    def test_has_openapi(self):
        self.assertTrue(self.plugin.has_openapi())

    def test_openapi_paths(self):
        paths = self.plugin.openapi_paths()
        self.assertIn("/openapi.json", paths)
