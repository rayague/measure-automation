from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from boundary_analyzer.auto.models import DetectionResult, EntryPoint
from boundary_analyzer.auto.plugins.base import Instrumentation, LanguagePlugin

logger = logging.getLogger(__name__)


_DEFAULT_PORTS: dict[str, int] = {
    "aspnet-core": 5000,
    "dotnet": 5000,
}


def _find_csproj_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.csproj"))


def _find_sln_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.sln"))


def _is_web_sdk(csproj: Path) -> bool:
    try:
        content = csproj.read_text(encoding="utf-8", errors="replace")
        return 'Sdk="Microsoft.NET.Sdk.Web"' in content
    except OSError:
        return False


def _has_aspnet_reference(csproj: Path) -> bool:
    try:
        content = csproj.read_text(encoding="utf-8", errors="replace")
        return "Microsoft.AspNetCore.App" in content or "Microsoft.AspNetCore" in content
    except OSError:
        return False


def _get_project_name(csproj: Path) -> str:
    return csproj.stem


def _find_program_cs(root: Path) -> Path | None:
    candidates = [
        root / "Program.cs",
        root / "program.cs",
    ]
    candidates.extend(sorted(root.rglob("Program.cs")))
    for p in candidates:
        if p.exists():
            return p
    return None


def _find_startup_cs(root: Path) -> Path | None:
    candidates = [
        root / "Startup.cs",
        root / "startup.cs",
    ]
    candidates.extend(sorted(root.rglob("Startup.cs")))
    for p in candidates:
        if p.exists():
            return p
    return None


def _find_launch_settings(root: Path) -> Path | None:
    candidates = sorted(root.rglob("Properties/launchSettings.json"))
    candidates.extend(sorted(root.rglob("Properties/launchsettings.json")))
    for p in candidates:
        if p.exists():
            return p
    return None


def _parse_launch_settings(settings_file: Path) -> str | None:
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8", errors="replace"))
        profiles = data.get("profiles", {})
        for name, profile in profiles.items():
            app_url = profile.get("applicationUrl", "")
            if app_url:
                urls = app_url.split(";")
                for url in urls:
                    m = re.search(r"https?://.*?:(\d+)", url)
                    if m:
                        return m.group(1)
        return None
    except (json.JSONDecodeError, OSError):
        return None


def _find_appsettings(root: Path) -> Path | None:
    candidates = [
        root / "appsettings.json",
    ]
    candidates.extend(sorted(root.rglob("appsettings.json")))
    for p in candidates:
        if p.exists():
            return p
    return None


def _parse_appsettings_for_urls(settings_file: Path) -> int | None:
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8", errors="replace"))
        urls = data.get("Urls", "")
        if urls:
            m = re.search(r"https?://.*?:(\d+)", urls)
            if m:
                return int(m.group(1))
        return None
    except (json.JSONDecodeError, OSError):
        return None


class DotNetPlugin(LanguagePlugin):
    @property
    def name(self) -> str:
        return "dotnet"

    def detect(self, root: Path) -> DetectionResult:
        if not root.exists():
            return DetectionResult(
                score=0.0,
                language="dotnet",
                framework="",
                detail="Directory not found",
            )

        csproj_files = _find_csproj_files(root)
        if not csproj_files:
            sln_files = _find_sln_files(root)
            if not sln_files:
                return DetectionResult(
                    score=0.0,
                    language="dotnet",
                    framework="",
                    detail="No .csproj or .sln files found",
                )
            return DetectionResult(
                score=0.3,
                language="dotnet",
                framework="dotnet",
                detail="Solution file found but no .csproj",
            )

        framework = "dotnet"
        entries: list[EntryPoint] = []

        for csproj in csproj_files:
            if _is_web_sdk(csproj) or _has_aspnet_reference(csproj):
                framework = "aspnet-core"
                entry = self._find_entry_for_csproj(root, csproj)
                if entry:
                    entries.append(entry)

        if not entries:
            entries = self._find_fallback_entries(root)

        score = 0.9 if entries else 0.7

        return DetectionResult(
            score=score,
            language="dotnet",
            framework=framework,
            entries=entries,
            build_tool="dotnet",
            detail=(f".NET {framework} with {len(entries)} entry point(s)" if entries else f".NET {framework}"),
        )

    def _find_entry_for_csproj(self, root: Path, csproj: Path) -> EntryPoint | None:
        csproj_dir = csproj.parent
        program = _find_program_cs(csproj_dir)
        if program:
            return EntryPoint(path=csproj, framework="aspnet-core")
        startup = _find_startup_cs(csproj_dir)
        if startup:
            return EntryPoint(path=csproj, framework="aspnet-core")
        return None

    def _find_fallback_entries(self, root: Path) -> list[EntryPoint]:
        entries: list[EntryPoint] = []
        program = _find_program_cs(root)
        if program:
            for csproj in _find_csproj_files(root):
                entries.append(EntryPoint(path=csproj, framework="dotnet"))
                break
        return entries

    def find_entry_points(self, root: Path) -> list[EntryPoint]:
        csproj_files = _find_csproj_files(root)
        entries: list[EntryPoint] = []
        for csproj in csproj_files:
            if _is_web_sdk(csproj) or _has_aspnet_reference(csproj):
                entry = self._find_entry_for_csproj(root, csproj)
                if entry:
                    entries.append(entry)
                    break
        if not entries:
            entries = self._find_fallback_entries(root)
        return entries

    def detect_framework(self, root: Path, entry: EntryPoint) -> str:
        return entry.framework or "dotnet"

    def instrument(
        self, entry: EntryPoint, service_name: str, otlp_endpoint: str = "http://localhost:4318"
    ) -> Instrumentation:
        return Instrumentation(
            env_vars={
                "OTEL_SERVICE_NAME": service_name,
                "OTEL_EXPORTER_OTLP_ENDPOINT": otlp_endpoint,
                "OTEL_DOTNET_AUTO_TRACES_EXPORTER": "otlp",
                "OTEL_DOTNET_AUTO_METRICS_EXPORTER": "none",
                "OTEL_DOTNET_AUTO_LOGS_EXPORTER": "none",
                "OTEL_DOTNET_AUTO_FLUSH_ON_UNHANDLEDEXCEPTION": "true",
            },
            files_to_install=["OpenTelemetry.AutoInstrumentation"],
            need_build=True,
        )

    def run_command(self, entry: EntryPoint, port: int | None = None) -> list[str] | None:
        port = port or self.guess_port(entry) or 5000
        csproj = entry.path
        return ["dotnet", "run", "--project", str(csproj), "--urls", f"http://0.0.0.0:{port}"]

    def install_command(self, root: Path) -> list[str] | None:
        csproj_files = _find_csproj_files(root)
        if not csproj_files:
            return None
        return ["dotnet", "restore"]

    def guess_port(self, entry: EntryPoint) -> int | None:
        csproj = entry.path
        csproj_dir = csproj.parent

        launch = _find_launch_settings(csproj_dir)
        if launch:
            port_str = _parse_launch_settings(launch)
            if port_str:
                return int(port_str)

        settings = _find_appsettings(csproj_dir)
        if settings:
            port = _parse_appsettings_for_urls(settings)
            if port is not None:
                return port

        fw = entry.framework or "dotnet"
        return _DEFAULT_PORTS.get(fw, 5000)

    def has_openapi(self) -> bool:
        return True

    def openapi_paths(self) -> list[str]:
        return [
            "/swagger/v1/swagger.json",
            "/swagger/index.html",
            "/swagger",
            "/api/swagger.json",
            "/openapi.json",
        ]
