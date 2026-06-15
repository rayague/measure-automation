import tempfile
import unittest
from pathlib import Path

from boundary_analyzer.auto.models import EntryPoint
from boundary_analyzer.auto.plugins.php import PhpPlugin


class PhpPluginTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="php_test_"))
        self.plugin = PhpPlugin()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, path: str, content: str) -> Path:
        full = self.tmpdir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return full

    def test_name(self):
        self.assertEqual(self.plugin.name, "php")

    def test_detect_no_dir(self):
        result = self.plugin.detect(Path(tempfile.gettempdir()) / "nonexistent" / "path" / "xyz")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.language, "php")

    def test_detect_no_files(self):
        self._write("readme.md", "# hello")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.0)
        self.assertIn("No composer.json", result.detail)

    def test_detect_php_files_no_composer(self):
        self._write("index.php", "<?php echo 'hello';")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.3)
        self.assertEqual(result.language, "php")
        self.assertEqual(result.framework, "php")

    def test_detect_composer_no_framework(self):
        content = """{"require": {"php": ">=8.0", "monolog/monolog": "^2.0"}}"""
        self._write("composer.json", content)
        self._write("index.php", "<?php echo 'hello';")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.9)
        self.assertEqual(result.language, "php")
        self.assertEqual(result.framework, "php")
        self.assertEqual(result.build_tool, "composer")

    def test_detect_laravel(self):
        content = """{"require": {"laravel/framework": "^10.0"}}"""
        self._write("composer.json", content)
        self._write("artisan", "#!/usr/bin/env php")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "laravel")
        self.assertEqual(result.build_tool, "composer")
        self.assertEqual(len(result.entries), 1)

    def test_detect_symfony(self):
        content = """{"require": {"symfony/framework-bundle": "^6.0"}}"""
        self._write("composer.json", content)
        self._write("bin/console", "#!/usr/bin/env php")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "symfony")
        self.assertEqual(result.build_tool, "composer")

    def test_detect_cakephp(self):
        content = """{"require": {"cakephp/cakephp": "^5.0"}}"""
        self._write("composer.json", content)
        self._write("index.php", "<?php echo 'hello';")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "cakephp")
        self.assertEqual(result.build_tool, "composer")

    def test_detect_slim(self):
        content = """{"require": {"slim/slim": "^4.0"}}"""
        self._write("composer.json", content)
        self._write("index.php", "<?php\n$app = new \\Slim\\App();")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "slim")
        self.assertEqual(result.build_tool, "composer")

    def test_detect_require_dev(self):
        content = """{"require-dev": {"laravel/framework": "^10.0"}}"""
        self._write("composer.json", content)
        self._write("artisan", "#!/usr/bin/env php")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "laravel")

    def test_find_entry_point_artisan(self):
        self._write("composer.json", '{"require": {"laravel/framework": "^10.0"}}')
        self._write("artisan", "#!/usr/bin/env php")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 1)
        self.assertIn("artisan", str(entries[0].path))
        self.assertEqual(entries[0].framework, "laravel")

    def test_find_entry_point_symfony_console(self):
        self._write("composer.json", '{"require": {"symfony/framework-bundle": "^6.0"}}')
        self._write("bin/console", "#!/usr/bin/env php")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 1)
        self.assertIn("console", str(entries[0].path))
        self.assertEqual(entries[0].framework, "symfony")

    def test_find_entry_point_public_index(self):
        self._write("composer.json", '{"require": {"laravel/framework": "^10.0"}}')
        self._write("artisan", "#!/usr/bin/env php")
        self._write("public/index.php", "<?php")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 2)

    def test_find_entry_point_route_detection(self):
        self._write("routes/web.php", "<?php\nRoute::get('/hello', function() { return 'hi'; });")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 1)
        self.assertIn("web.php", str(entries[0].path))
        self.assertEqual(entries[0].framework, "php")

    def test_find_entry_point_no_entries(self):
        self._write("composer.json", '{"require": {"php": ">=8.0"}}')
        self._write("src/Lib.php", "<?php\nclass Lib {}")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 0)

    def test_detect_framework_from_entry(self):
        entry = EntryPoint(path=Path("artisan"), framework="laravel")
        self.assertEqual(self.plugin.detect_framework(self.tmpdir, entry), "laravel")

    def test_instrument_returns_otel_env(self):
        entry = EntryPoint(path=Path("artisan"), framework="laravel")
        instr = self.plugin.instrument(entry, "my-service")
        self.assertEqual(instr.env_vars["OTEL_SERVICE_NAME"], "my-service")
        self.assertIn("OTEL_EXPORTER_OTLP_ENDPOINT", instr.env_vars)
        self.assertIn("open-telemetry/opentelemetry", instr.files_to_install)
        self.assertIn("open-telemetry/opentelemetry-auto-php", instr.files_to_install)
        self.assertEqual(instr.env_vars["OTEL_METRICS_EXPORTER"], "none")
        self.assertEqual(instr.env_vars["OTEL_LOGS_EXPORTER"], "none")

    def test_run_command_artisan(self):
        entry = EntryPoint(path=self.tmpdir / "artisan", framework="laravel")
        cmd = self.plugin.run_command(entry, port=8000)
        self.assertIsNotNone(cmd)
        self.assertIn("php", cmd)
        self.assertIn(str(entry.path), cmd)
        self.assertIn("serve", cmd)
        self.assertIn("--port=8000", cmd)

    def test_run_command_symfony_console(self):
        entry = EntryPoint(path=self.tmpdir / "bin" / "console", framework="symfony")
        cmd = self.plugin.run_command(entry, port=8001)
        self.assertIsNotNone(cmd)
        self.assertIn("php", cmd)
        self.assertIn("server:start", cmd)

    def test_run_command_index_php(self):
        entry = EntryPoint(path=Path("public/index.php"), framework="php")
        cmd = self.plugin.run_command(entry, port=8080)
        self.assertIsNotNone(cmd)
        self.assertIn("php", cmd)
        self.assertIn("-S", cmd)
        self.assertIn("0.0.0.0:8080", cmd)

    def test_run_command_generic(self):
        entry = EntryPoint(path=Path("server.php"), framework="php")
        cmd = self.plugin.run_command(entry, port=9000)
        self.assertIsNotNone(cmd)
        self.assertIn("php", cmd)
        self.assertIn("-S", cmd)
        self.assertIn("server.php", str(cmd))

    def test_install_command_with_lock(self):
        self._write("composer.json", '{"require": {"php": ">=8.0"}}')
        self._write("composer.lock", "")
        cmd = self.plugin.install_command(self.tmpdir)
        self.assertIsNotNone(cmd)
        self.assertIn("--prefer-dist", str(cmd))

    def test_install_command_without_lock(self):
        self._write("composer.json", '{"require": {"php": ">=8.0"}}')
        cmd = self.plugin.install_command(self.tmpdir)
        self.assertIsNotNone(cmd)
        self.assertNotIn("--prefer-dist", str(cmd))

    def test_install_command_no_composer(self):
        cmd = self.plugin.install_command(self.tmpdir)
        self.assertIsNone(cmd)

    def test_guess_port_default_laravel(self):
        entry = EntryPoint(path=Path("artisan"), framework="laravel")
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 8000)

    def test_guess_port_default_symfony(self):
        entry = EntryPoint(path=Path("bin/console"), framework="symfony")
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 8000)

    def test_guess_port_default_cakephp(self):
        entry = EntryPoint(path=Path("index.php"), framework="cakephp")
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 8765)

    def test_guess_port_default_slim(self):
        entry = EntryPoint(path=Path("index.php"), framework="slim")
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 8080)

    def test_guess_port_from_env(self):
        self._write(".env", "APP_PORT=4000")
        entry = EntryPoint(path=self.tmpdir / "artisan", framework="laravel")
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 4000)

    def test_guess_port_from_env_other_keys(self):
        self._write(".env", "PORT=5000\nDB_HOST=localhost")
        entry = EntryPoint(path=self.tmpdir / "server.php", framework="php")
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 5000)

    def test_has_openapi(self):
        self.assertTrue(self.plugin.has_openapi())

    def test_openapi_paths(self):
        paths = self.plugin.openapi_paths()
        self.assertIn("/api/documentation", paths)
        self.assertIn("/swagger.json", paths)
        self.assertIn("/openapi.json", paths)
