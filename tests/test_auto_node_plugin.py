import tempfile
import unittest
from pathlib import Path

from boundary_analyzer.auto.models import EntryPoint
from boundary_analyzer.auto.plugins.node import NodePlugin


class NodePluginTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="node_test_"))
        self.plugin = NodePlugin()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, path: str, content: str) -> Path:
        full = self.tmpdir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return full

    def test_name(self):
        self.assertEqual(self.plugin.name, "node")

    def test_detect_no_dir(self):
        result = self.plugin.detect(Path(tempfile.gettempdir()) / "nonexistent" / "path" / "xyz")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.language, "node")

    def test_detect_no_js_files(self):
        self._write("readme.md", "# hello")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.0)

    def test_detect_js_files_no_package_json(self):
        self._write("index.js", "console.log('hello')")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.3)
        self.assertEqual(result.language, "node")

    def test_detect_express_from_deps(self):
        self._write(
            "package.json",
            """{
            "name": "demo",
            "dependencies": { "express": "^4.18.0" }
        }""",
        )
        self._write("index.js", "const express = require('express')")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.9)
        self.assertEqual(result.language, "node")
        self.assertEqual(result.framework, "express")

    def test_detect_fastify_from_deps(self):
        self._write(
            "package.json",
            """{
            "name": "demo",
            "dependencies": { "fastify": "^4.0.0" }
        }""",
        )
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "fastify")

    def test_detect_nestjs_from_deps(self):
        self._write(
            "package.json",
            """{
            "name": "demo",
            "dependencies": { "@nestjs/core": "^10.0.0" }
        }""",
        )
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "nestjs")

    def test_detect_koa_from_deps(self):
        self._write(
            "package.json",
            """{
            "name": "demo",
            "dependencies": { "koa": "^2.0.0" }
        }""",
        )
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "koa")

    def test_detect_unknown_framework(self):
        self._write(
            "package.json",
            """{
            "name": "demo",
            "dependencies": { "lodash": "^4.17.0" }
        }""",
        )
        self._write("index.js", "const _ = require('lodash')")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "node")

    def test_find_entry_points_from_main_field(self):
        self._write(
            "package.json",
            """{
            "name": "demo",
            "main": "server.js",
            "dependencies": { "express": "^4.18.0" }
        }""",
        )
        self._write("server.js", "const express = require('express')")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 1)
        self.assertIn("server.js", str(entries[0].path))

    def test_find_entry_points_common_names(self):
        self._write(
            "package.json",
            """{
            "name": "demo",
            "dependencies": { "express": "^4.18.0" }
        }""",
        )
        self._write("app.js", "const express = require('express')")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 1)
        self.assertIn("app.js", str(entries[0].path))

    def test_find_entry_points_no_entry(self):
        self._write("package.json", """{ "name": "demo" }""")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 0)

    def test_guess_port_default(self):
        self._write(
            "package.json",
            """{
            "name": "demo",
            "dependencies": { "express": "^4.18.0" }
        }""",
        )
        self._write("server.js", "const express = require('express')")
        entry = self.plugin.find_entry_points(self.tmpdir)[0]
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 3000)

    def test_guess_port_from_env(self):
        self._write("package.json", """{ "name": "demo" }""")
        self._write("index.js", "console.log('hi')")
        self._write(".env", "PORT=4000")
        entry = EntryPoint(path=self.tmpdir / "index.js", framework="node")
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 4000)

    def test_guess_port_nestjs_default(self):
        self._write(
            "package.json",
            """{
            "name": "demo",
            "dependencies": { "@nestjs/core": "^10.0.0" }
        }""",
        )
        self._write("main.ts", "import { NestFactory } from '@nestjs/core'")
        entries = self.plugin.find_entry_points(self.tmpdir)
        entry = entries[0] if entries else EntryPoint(path=self.tmpdir / "main.ts", framework="nestjs")
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 3001)

    def test_instrument_returns_env_vars(self):
        entry = EntryPoint(path=Path("server.js"), framework="express")
        instr = self.plugin.instrument(entry, "my-api")
        self.assertEqual(instr.env_vars["OTEL_SERVICE_NAME"], "my-api")
        self.assertIn("OTEL_EXPORTER_OTLP_ENDPOINT", instr.env_vars)
        self.assertIn("NODE_OPTIONS", instr.env_vars)
        self.assertIn("auto-instrumentations-node", instr.env_vars["NODE_OPTIONS"])
        self.assertTrue(len(instr.files_to_install) > 0)

    def test_run_command_node_direct(self):
        self._write("index.js", "console.log('hi')")
        entry = EntryPoint(path=self.tmpdir / "index.js", framework="node")
        cmd = self.plugin.run_command(entry)
        self.assertIsNotNone(cmd)
        self.assertIn("node", cmd)
        self.assertIn("index.js", cmd[-1])

    def test_run_command_npm_start(self):
        self._write(
            "package.json",
            """{
            "name": "demo",
            "scripts": { "start": "node server.js" }
        }""",
        )
        self._write("server.js", "const express = require('express')")
        entry = EntryPoint(path=self.tmpdir / "server.js", framework="express")
        cmd = self.plugin.run_command(entry)
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd, ["npm", "start"])

    def test_install_command_npm_install(self):
        self._write("package.json", """{ "name": "demo" }""")
        cmd = self.plugin.install_command(self.tmpdir)
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd, ["npm", "install"])

    def test_install_command_npm_ci(self):
        self._write("package.json", """{ "name": "demo" }""")
        self._write("package-lock.json", "{}")
        cmd = self.plugin.install_command(self.tmpdir)
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd, ["npm", "ci"])

    def test_install_command_no_package_json(self):
        cmd = self.plugin.install_command(self.tmpdir)
        self.assertIsNone(cmd)

    def test_has_openapi(self):
        self.assertTrue(self.plugin.has_openapi())

    def test_openapi_paths(self):
        paths = self.plugin.openapi_paths()
        self.assertIn("/api-docs", paths)
        self.assertIn("/swagger.json", paths)
        self.assertIn("/openapi.json", paths)
