import tempfile
import unittest
from pathlib import Path

from boundary_analyzer.auto.models import EntryPoint
from boundary_analyzer.auto.plugins.java import JavaPlugin


class JavaPluginTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="java_test_"))
        self.plugin = JavaPlugin()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, path: str, content: str) -> Path:
        full = self.tmpdir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return full

    def test_name(self):
        self.assertEqual(self.plugin.name, "java")

    def test_detect_no_dir(self):
        result = self.plugin.detect(Path(tempfile.gettempdir()) / "nonexistent" / "path" / "xyz")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.language, "java")

    def test_detect_no_java_files(self):
        self._write("readme.md", "# hello")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.0)
        self.assertIn("No Java build files", result.detail)

    def test_detect_java_files_no_build(self):
        self._write("src/Main.java", "public class Main {}")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.3)
        self.assertEqual(result.language, "java")

    def test_detect_maven_project(self):
        self._write(
            "pom.xml",
            """<?xml version="1.0"?>
<project><groupId>test</groupId><artifactId>demo</artifactId></project>""",
        )
        self._write("src/main/java/com/demo/Application.java", "package com.demo;\npublic class Application {}")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.7)
        self.assertEqual(result.language, "java")
        self.assertEqual(result.build_tool, "maven")

    def test_detect_gradle_project(self):
        self._write(
            "build.gradle",
            """plugins { id 'java' }
group = 'com.demo'
version = '1.0'""",
        )
        self._write("src/main/java/com/demo/App.java", "package com.demo;\npublic class App {}")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.7)
        self.assertEqual(result.language, "java")
        self.assertEqual(result.build_tool, "gradle")

    def test_detect_spring_boot_from_pom(self):
        self._write(
            "pom.xml",
            """<?xml version="1.0"?>
<project>
    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
    </parent>
    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>
    </dependencies>
</project>""",
        )
        self._write(
            "src/main/java/com/demo/Application.java",
            "package com.demo;\nimport org.springframework.boot.SpringApplication;\n@SpringBootApplication\npublic class Application {\n    public static void main(String[] args) {}\n}",
        )
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "spring-boot")
        self.assertEqual(result.build_tool, "maven")
        self.assertEqual(len(result.entries), 1)
        self.assertIn("Application.java", str(result.entries[0].path))

    def test_detect_spring_boot_from_gradle(self):
        self._write(
            "build.gradle",
            """plugins {
    id 'org.springframework.boot' version '3.2.0'
    id 'io.spring.dependency-management' version '1.1.4'
    id 'java'
}
dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-web'
}""",
        )
        self._write(
            "src/main/java/com/demo/DemoApplication.java",
            "package com.demo;\n@SpringBootApplication\npublic class DemoApplication {\n    public static void main(String[] args) {}\n}",
        )
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "spring-boot")
        self.assertEqual(result.build_tool, "gradle")

    def test_detect_no_spring_boot_project(self):
        self._write(
            "pom.xml",
            """<?xml version="1.0"?>
<project><groupId>test</groupId><artifactId>demo</artifactId>
    <dependencies>
        <dependency><groupId>org.apache.commons</groupId><artifactId>commons-lang3</artifactId></dependency>
    </dependencies>
</project>""",
        )
        self._write("src/main/java/com/demo/Main.java", "package com.demo;\npublic class Main {\n    public static void main(String[] args) {}\n}")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "java")
        self.assertEqual(len(result.entries), 1)

    def test_find_entry_points_main_class(self):
        self._write("pom.xml", "<project></project>")
        self._write("src/main/java/com/demo/Main.java", "package com.demo;\npublic class Main {\n    public static void main(String[] args) {}\n}")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 1)
        self.assertIn("Main.java", str(entries[0].path))

    def test_find_entry_points_no_main(self):
        self._write("pom.xml", "<project></project>")
        self._write("src/main/java/com/demo/Utils.java", 'package com.demo;\npublic class Utils {\n    public String greet() { return "hi"; }\n}')
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 0)

    def test_detect_framework_from_entry(self):
        self._write("pom.xml", "<project></project>")
        self._write("src/main/java/App.java", "package com.demo;\n@SpringBootApplication\npublic class App {\n    public static void main(String[] args) {}\n}")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].framework, "spring-boot")

    def test_instrument_returns_otel_env(self):
        entry = EntryPoint(path=Path("App.java"), framework="spring-boot")
        instr = self.plugin.instrument(entry, "my-service")
        self.assertEqual(instr.env_vars["OTEL_SERVICE_NAME"], "my-service")
        self.assertIn("OTEL_EXPORTER_OTLP_ENDPOINT", instr.env_vars)
        self.assertEqual(instr.env_vars["OTEL_METRICS_EXPORTER"], "none")
        self.assertTrue(instr.need_build)

    def test_run_command_returns_none(self):
        entry = EntryPoint(path=Path("App.java"), framework="spring-boot")
        cmd = self.plugin.run_command(entry)
        self.assertIsNone(cmd)

    def test_install_command_maven(self):
        self._write("pom.xml", "<project></project>")
        cmd = self.plugin.install_command(self.tmpdir)
        self.assertIsNotNone(cmd)
        self.assertIn("package", cmd)

    def test_install_command_gradle(self):
        self._write("build.gradle", "plugins { id 'java' }")
        cmd = self.plugin.install_command(self.tmpdir)
        self.assertIsNotNone(cmd)
        self.assertIn("build", cmd)

    def test_install_command_no_build_file(self):
        cmd = self.plugin.install_command(self.tmpdir)
        self.assertIsNone(cmd)

    def test_guess_port_default(self):
        self._write("pom.xml", "<project></project>")
        self._write("src/main/java/App.java", "package com.demo;\n@SpringBootApplication\npublic class App {\n    public static void main(String[] args) {}\n}")
        entry = self.plugin.find_entry_points(self.tmpdir)[0]
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 8080)

    def test_guess_port_from_application_properties(self):
        self._write("pom.xml", "<project></project>")
        self._write("src/main/resources/application.properties", "server.port=9090\nspring.application.name=demo")
        self._write("src/main/java/App.java", "package com.demo;\n@SpringBootApplication\npublic class App {\n    public static void main(String[] args) {}\n}")
        entry = self.plugin.find_entry_points(self.tmpdir)[0]
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 9090)

    def test_guess_port_non_spring(self):
        self._write("pom.xml", "<project></project>")
        self._write("src/main/java/Main.java", "package com.demo;\npublic class Main {\n    public static void main(String[] args) {}\n}")
        entry = self.plugin.find_entry_points(self.tmpdir)[0]
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 8080)

    def test_has_openapi(self):
        self.assertTrue(self.plugin.has_openapi())

    def test_openapi_paths(self):
        paths = self.plugin.openapi_paths()
        self.assertIn("/v3/api-docs", paths)
        self.assertIn("/swagger-ui.html", paths)
