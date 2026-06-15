from __future__ import annotations

import logging
import re
from pathlib import Path

from boundary_analyzer.auto.models import DetectionResult, EntryPoint
from boundary_analyzer.auto.plugins.base import Instrumentation, LanguagePlugin

logger = logging.getLogger(__name__)


_JAVA_BUILD_FILES = [
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
]

_FRAMEWORK_INDICATORS: dict[str, list[str]] = {
    "spring-boot": [
        "spring-boot-starter-web",
        "spring-boot-starter",
        "@SpringBootApplication",
        "@EnableAutoConfiguration",
    ],
    "micronaut": [
        "micronaut-http-server-netty",
        "micronaut-inject",
    ],
    "quarkus": [
        "quarkus-resteasy",
        "quarkus-resteasy-reactive",
    ],
    "jakarta-ee": [
        "jakarta.ws.rs-api",
        "jakarta.servlet-api",
    ],
}

_FRAMEWORK_PORTS: dict[str, int] = {
    "spring-boot": 8080,
    "micronaut": 8080,
    "quarkus": 8080,
    "jakarta-ee": 8080,
    "java": 8080,
}

_AGENT_DIR = Path.home() / ".mba" / "agents"
_AGENT_JAR = _AGENT_DIR / "opentelemetry-javaagent.jar"
_AGENT_URL = (
    "https://github.com/open-telemetry/opentelemetry-java-instrumentation/"
    "releases/latest/download/opentelemetry-javaagent.jar"
)


def _find_build_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in _JAVA_BUILD_FILES:
        p = root / name
        if p.exists():
            files.append(p)
    return files


def _detect_build_tool(root: Path) -> str:
    if (root / "pom.xml").exists():
        return "maven"
    for name in ["build.gradle", "build.gradle.kts"]:
        if (root / name).exists():
            return "gradle"
    return ""


def _find_java_sources(root: Path) -> list[Path]:
    return sorted(root.rglob("*.java"))


def _find_main_classes(root: Path) -> list[Path]:
    results: list[Path] = []
    for jf in _find_java_sources(root):
        try:
            content = jf.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        if re.search(r"public\s+static\s+void\s+main\s*\(\s*String\s*\[\s*\]", content):
            results.append(jf)
    return results


def _find_spring_boot_applications(root: Path) -> list[Path]:
    results: list[Path] = []
    for jf in _find_java_sources(root):
        try:
            content = jf.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        if "@SpringBootApplication" in content:
            results.append(jf)
    if not results:
        results = _find_main_classes(root)
    return results


def _scan_sources_for_framework(root: Path) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for jf in _find_java_sources(root):
        try:
            content = jf.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for fw, indicators in _FRAMEWORK_INDICATORS.items():
            for ind in indicators:
                if ind in content:
                    found.setdefault(fw, set()).add(str(jf))
    return found


def _detect_framework_from_code(frameworks_found: dict[str, set[str]]) -> str:
    priority = ["spring-boot", "micronaut", "quarkus", "jakarta-ee"]
    for fw in priority:
        if fw in frameworks_found:
            return fw
    return "java"


def _detect_framework_in_pom(root: Path) -> str:
    pom = root / "pom.xml"
    if not pom.exists():
        return ""
    try:
        content = pom.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return ""
    for fw, indicators in _FRAMEWORK_INDICATORS.items():
        for ind in indicators:
            if ind in content:
                return fw
    return ""


def _detect_framework_in_gradle(root: Path) -> str:
    for name in ["build.gradle", "build.gradle.kts"]:
        gf = root / name
        if not gf.exists():
            continue
        try:
            content = gf.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for fw, indicators in _FRAMEWORK_INDICATORS.items():
            for ind in indicators:
                if ind in content:
                    return fw
    return ""


def _scan_config_for_port(root: Path) -> int | None:
    candidates = [
        root / "src" / "main" / "resources" / "application.properties",
        root / "src" / "main" / "resources" / "application.yml",
        root / "src" / "main" / "resources" / "application.yaml",
        root / "application.properties",
    ]
    for cfg in candidates:
        if not cfg.exists():
            continue
        try:
            content = cfg.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        m = re.search(r"(?:^|\n)server\.port\s*[=:]\s*(\d+)", content)
        if m:
            return int(m.group(1))
    return None


def _find_project_root(entry_path: Path) -> Path:
    for parent in [entry_path] + list(entry_path.parents):
        for marker in ["pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle"]:
            if (parent / marker).exists():
                return parent
    return entry_path.parent


def _detect_framework_in_file(java_file: Path) -> str:
    try:
        content = java_file.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return "java"
    for fw, indicators in _FRAMEWORK_INDICATORS.items():
        for ind in indicators:
            if ind in content:
                return fw
    return "java"


class JavaPlugin(LanguagePlugin):
    @property
    def name(self) -> str:
        return "java"

    def detect(self, root: Path) -> DetectionResult:
        if not root.exists():
            return DetectionResult(
                score=0.0,
                language="java",
                framework="",
                detail="Directory not found",
            )

        build_files = _find_build_files(root)
        if not build_files:
            java_files = _find_java_sources(root)
            if not java_files:
                return DetectionResult(
                    score=0.0,
                    language="java",
                    framework="",
                    detail="No Java build files (pom.xml, build.gradle) or .java files found",
                )
            return DetectionResult(
                score=0.3,
                language="java",
                framework="java",
                detail="Java files found but no standard build file",
            )

        build_tool = _detect_build_tool(root)

        framework = ""
        if build_tool == "maven":
            framework = _detect_framework_in_pom(root)
        elif build_tool == "gradle":
            framework = _detect_framework_in_gradle(root)

        if not framework:
            frameworks_found = _scan_sources_for_framework(root)
            framework = _detect_framework_from_code(frameworks_found)

        entries = self.find_entry_points(root)
        score = 0.9 if entries else 0.7

        return DetectionResult(
            score=score,
            language="java",
            framework=framework or "java",
            entries=entries,
            build_tool=build_tool,
            detail=(
                f"Java {framework or 'project'} ({build_tool}) with {len(entries)} entry point(s)"
                if entries
                else f"Java {framework or 'project'} ({build_tool})"
            ),
        )

    def find_entry_points(self, root: Path) -> list[EntryPoint]:
        candidates: list[EntryPoint] = []

        spring_apps = _find_spring_boot_applications(root)
        for app in spring_apps:
            candidates.append(EntryPoint(path=app, framework="spring-boot"))

        if candidates:
            return candidates

        main_classes = _find_main_classes(root)
        for mc in main_classes:
            fw = _detect_framework_in_file(mc)
            candidates.append(EntryPoint(path=mc, framework=fw))

        return candidates

    def detect_framework(self, root: Path, entry: EntryPoint) -> str:
        build_tool = _detect_build_tool(root)
        if build_tool == "maven":
            fw = _detect_framework_in_pom(root)
            if fw:
                return fw
        elif build_tool == "gradle":
            fw = _detect_framework_in_gradle(root)
            if fw:
                return fw
        return entry.framework or "java"

    def instrument(
        self, entry: EntryPoint, service_name: str, otlp_endpoint: str = "http://localhost:4318"
    ) -> Instrumentation:
        return Instrumentation(
            env_vars={
                "OTEL_SERVICE_NAME": service_name,
                "OTEL_EXPORTER_OTLP_ENDPOINT": otlp_endpoint,
                "OTEL_METRICS_EXPORTER": "none",
                "OTEL_LOGS_EXPORTER": "none",
            },
            need_build=True,
        )

    def run_command(self, entry: EntryPoint, port: int | None = None) -> list[str] | None:
        return None

    def install_command(self, root: Path) -> list[str] | None:
        mvnw_cmd = _find_wrapper(root, "mvnw")
        if mvnw_cmd:
            return [str(mvnw_cmd), "package", "-DskipTests"]

        gradlew_cmd = _find_wrapper(root, "gradlew")
        if gradlew_cmd:
            return [str(gradlew_cmd), "build", "-x", "test"]

        build_tool = _detect_build_tool(root)
        if build_tool == "maven":
            return ["mvn", "package", "-DskipTests"]
        elif build_tool == "gradle":
            return ["gradle", "build", "-x", "test"]

        return None

    def guess_port(self, entry: EntryPoint) -> int | None:
        project_root = _find_project_root(entry.path)
        config_port = _scan_config_for_port(project_root)
        if config_port is not None:
            return config_port

        fw = entry.framework or _detect_framework_in_file(entry.path)
        return _FRAMEWORK_PORTS.get(fw)

    def has_openapi(self) -> bool:
        return True

    def openapi_paths(self) -> list[str]:
        return [
            "/v3/api-docs",
            "/v2/api-docs",
            "/swagger-ui.html",
            "/swagger-resources",
            "/api-docs",
        ]


def _find_wrapper(root: Path, name: str) -> Path | None:
    import platform as _platform

    cmd = root / name
    cmd_bat = root / f"{name}.cmd"
    if _platform.system() == "Windows":
        return cmd_bat if cmd_bat.exists() else (cmd if cmd.exists() else None)
    return cmd if cmd.exists() else (cmd_bat if cmd_bat.exists() else None)
