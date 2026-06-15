import tempfile
import unittest
from pathlib import Path

from boundary_analyzer.auto.models import EntryPoint
from boundary_analyzer.auto.plugins.dotnet import DotNetPlugin


class DotNetPluginTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="dotnet_test_"))
        self.plugin = DotNetPlugin()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, path: str, content: str) -> Path:
        full = self.tmpdir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return full

    def test_name(self):
        self.assertEqual(self.plugin.name, "dotnet")

    def test_detect_no_dir(self):
        result = self.plugin.detect(Path(tempfile.gettempdir()) / "nonexistent" / "path" / "xyz")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.language, "dotnet")

    def test_detect_no_files(self):
        self._write("readme.md", "# hello")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.0)
        self.assertIn("No .csproj", result.detail)

    def test_detect_sln_only(self):
        self._write("demo.sln", "")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.3)
        self.assertEqual(result.language, "dotnet")
        self.assertEqual(result.framework, "dotnet")

    def test_detect_csproj_console(self):
        self._write("demo.csproj", '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType></PropertyGroup></Project>')
        self._write("Program.cs", 'Console.WriteLine("Hello");')
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.9)
        self.assertEqual(result.language, "dotnet")
        self.assertEqual(result.framework, "dotnet")
        self.assertEqual(result.build_tool, "dotnet")
        self.assertEqual(len(result.entries), 1)
        self.assertIn("demo.csproj", str(result.entries[0].path))

    def test_detect_aspnet_core(self):
        self._write("WebApp.csproj", '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>')
        self._write("Program.cs", "var builder = WebApplication.CreateBuilder(args);\nvar app = builder.Build();\napp.Run();")
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "aspnet-core")
        self.assertEqual(result.build_tool, "dotnet")
        self.assertEqual(len(result.entries), 1)

    def test_detect_aspnet_with_reference(self):
        self._write(
            "WebApp.csproj", '<Project Sdk="Microsoft.NET.Sdk"><ItemGroup><FrameworkReference Include="Microsoft.AspNetCore.App" /></ItemGroup></Project>'
        )
        self._write("Program.cs", 'Console.WriteLine("ASP.NET");')
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "aspnet-core")

    def test_detect_multi_csproj(self):
        self._write(
            "src/WebApp/WebApp.csproj",
            '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>',
        )
        self._write("src/WebApp/Program.cs", "var builder = WebApplication.CreateBuilder(args);\nvar app = builder.Build();\napp.Run();")
        self._write("src/Lib/Lib.csproj", '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>')
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.framework, "aspnet-core")
        self.assertEqual(len(result.entries), 1)

    def test_detect_no_entries(self):
        self._write("demo.csproj", '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType></PropertyGroup></Project>')
        result = self.plugin.detect(self.tmpdir)
        self.assertEqual(result.score, 0.7)
        self.assertEqual(len(result.entries), 0)

    def test_find_entry_points_aspnet(self):
        self._write("WebApp.csproj", '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>')
        self._write("Program.cs", "var builder = WebApplication.CreateBuilder(args);\napp.Run();")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 1)
        self.assertIn("WebApp.csproj", str(entries[0].path))

    def test_find_entry_points_startup(self):
        self._write("OldWeb.csproj", '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup><TargetFramework>net6.0</TargetFramework></PropertyGroup></Project>')
        self._write("Startup.cs", "public class Startup { public void Configure() {} }")
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 1)

    def test_find_entry_points_no_program(self):
        self._write("Console.csproj", '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType></PropertyGroup></Project>')
        entries = self.plugin.find_entry_points(self.tmpdir)
        self.assertEqual(len(entries), 0)

    def test_detect_framework_from_entry(self):
        entry = EntryPoint(path=Path("WebApp.csproj"), framework="aspnet-core")
        self.assertEqual(self.plugin.detect_framework(self.tmpdir, entry), "aspnet-core")

    def test_instrument_returns_otel_env(self):
        entry = EntryPoint(path=Path("WebApp.csproj"), framework="aspnet-core")
        instr = self.plugin.instrument(entry, "my-service")
        self.assertEqual(instr.env_vars["OTEL_SERVICE_NAME"], "my-service")
        self.assertIn("OTEL_EXPORTER_OTLP_ENDPOINT", instr.env_vars)
        self.assertEqual(instr.env_vars["OTEL_DOTNET_AUTO_TRACES_EXPORTER"], "otlp")
        self.assertEqual(instr.env_vars["OTEL_DOTNET_AUTO_METRICS_EXPORTER"], "none")
        self.assertEqual(instr.env_vars["OTEL_DOTNET_AUTO_LOGS_EXPORTER"], "none")
        self.assertIn("OpenTelemetry.AutoInstrumentation", instr.files_to_install)
        self.assertTrue(instr.need_build)

    def test_run_command(self):
        entry = EntryPoint(path=self.tmpdir / "WebApp.csproj", framework="aspnet-core")
        cmd = self.plugin.run_command(entry, port=5000)
        self.assertIsNotNone(cmd)
        self.assertIn("dotnet", cmd)
        self.assertIn("run", cmd)
        self.assertIn("--project", cmd)
        self.assertIn(str(entry.path), cmd)
        self.assertIn("--urls", cmd)
        self.assertIn("http://0.0.0.0:5000", cmd)

    def test_run_command_default_port(self):
        entry = EntryPoint(path=Path("WebApp.csproj"), framework="aspnet-core")
        cmd = self.plugin.run_command(entry)
        self.assertIsNotNone(cmd)
        self.assertIn("http://0.0.0.0:5000", cmd)

    def test_install_command(self):
        self._write("WebApp.csproj", '<Project Sdk="Microsoft.NET.Sdk.Web" />')
        cmd = self.plugin.install_command(self.tmpdir)
        self.assertIsNotNone(cmd)
        self.assertIn("dotnet", cmd)
        self.assertIn("restore", cmd)

    def test_install_command_no_csproj(self):
        cmd = self.plugin.install_command(self.tmpdir)
        self.assertIsNone(cmd)

    def test_guess_port_default(self):
        entry = EntryPoint(path=Path("WebApp.csproj"), framework="aspnet-core")
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 5000)

    def test_guess_port_console_default(self):
        entry = EntryPoint(path=Path("demo.csproj"), framework="dotnet")
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 5000)

    def test_guess_port_from_launch_settings(self):
        self._write("Properties/launchSettings.json", '{"profiles": {"WebApp": {"applicationUrl": "http://localhost:8080"}}}')
        entry = EntryPoint(path=self.tmpdir / "WebApp.csproj", framework="aspnet-core")
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 8080)

    def test_guess_port_from_launch_settings_multi(self):
        self._write("Properties/launchSettings.json", '{"profiles": {"WebApp": {"applicationUrl": "https://localhost:7001;http://localhost:8000"}}}')
        entry = EntryPoint(path=self.tmpdir / "WebApp.csproj", framework="aspnet-core")
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 7001)

    def test_guess_port_from_appsettings(self):
        self._write("appsettings.json", '{"Urls": "http://0.0.0.0:9090"}')
        entry = EntryPoint(path=self.tmpdir / "WebApp.csproj", framework="aspnet-core")
        port = self.plugin.guess_port(entry)
        self.assertEqual(port, 9090)

    def test_has_openapi(self):
        self.assertTrue(self.plugin.has_openapi())

    def test_openapi_paths(self):
        paths = self.plugin.openapi_paths()
        self.assertIn("/swagger/v1/swagger.json", paths)
        self.assertIn("/swagger/index.html", paths)
