from __future__ import annotations

import logging
import re
from pathlib import Path

from boundary_analyzer.auto.models import DetectionResult, EntryPoint
from boundary_analyzer.auto.plugins.base import Instrumentation, LanguagePlugin

logger = logging.getLogger(__name__)


_FRAMEWORK_DEPENDENCIES: dict[str, list[str]] = {
    "laravel": ["laravel/framework"],
    "symfony": ["symfony/framework-bundle", "symfony/http-kernel"],
    "cakephp": ["cakephp/cakephp"],
    "slim": ["slim/slim"],
}

_FRAMEWORK_PORTS: dict[str, int] = {
    "laravel": 8000,
    "symfony": 8000,
    "cakephp": 8765,
    "slim": 8080,
    "php": 8080,
}

_FRAMEWORK_ARTISAN_COMMANDS: dict[str, str] = {
    "laravel": "artisan",
    "symfony": "bin/console",
}


def _find_composer_json(root: Path) -> Path | None:
    p = root / "composer.json"
    return p if p.exists() else None


def _read_composer_json(root: Path) -> dict | None:
    p = _find_composer_json(root)
    if p is None:
        return None
    try:
        import json

        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError) as e:
        logger.warning("Failed to read composer.json: %s", e)
        return None


def _detect_framework_from_composer(composer: dict) -> str:
    all_deps: dict[str, str] = {}
    for section in ["require", "require-dev"]:
        deps = composer.get(section, {})
        if isinstance(deps, dict):
            all_deps.update(deps)

    priority = ["laravel", "symfony", "cakephp", "slim"]
    for fw in priority:
        indicators = _FRAMEWORK_DEPENDENCIES[fw]
        for ind in indicators:
            if ind in all_deps:
                return fw
    return ""


def _find_php_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.php"))


def _scan_sources_for_framework(root: Path) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for php_file in _find_php_files(root):
        try:
            content = php_file.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for fw, indicators in _FRAMEWORK_DEPENDENCIES.items():
            for ind in indicators:
                if ind in content:
                    found.setdefault(fw, set()).add(str(php_file))
    return found


def _detect_framework_from_code(frameworks_found: dict[str, set[str]]) -> str:
    priority = ["laravel", "symfony", "cakephp", "slim"]
    for fw in priority:
        if fw in frameworks_found:
            return fw
    return "php"


def _find_entry_points_common(root: Path) -> list[EntryPoint]:
    candidates: list[EntryPoint] = []
    # Laravel artisan
    artisan = root / "artisan"
    if artisan.exists():
        fw = ""
        composer = _read_composer_json(root)
        if composer:
            fw = _detect_framework_from_composer(composer)
        candidates.append(EntryPoint(path=artisan, framework=fw or "laravel"))
    # Symfony console
    console = root / "bin" / "console"
    if console.exists():
        candidates.append(EntryPoint(path=console, framework="symfony"))
    # public/index.php (Laravel, Symfony)
    index = root / "public" / "index.php"
    if index.exists():
        fw = "laravel" if (root / "artisan").exists() else "symfony"
        candidates.append(EntryPoint(path=index, framework=fw))
    # index.php at root
    root_index = root / "index.php"
    if root_index.exists() and not any(c.path == root_index for c in candidates):
        fw = _detect_framework_in_file(root_index)
        candidates.append(EntryPoint(path=root_index, framework=fw))
    return candidates


def _find_entry_points_by_routes(root: Path) -> list[EntryPoint]:
    results: list[EntryPoint] = []
    route_patterns = [
        r"\$app->(?:get|post|put|patch|delete|any)\s*\(",
        r"Route::(?:get|post|put|patch|delete|any)\s*\(",
        r"#[A-Za-z]+\s*Route\s*\(",
    ]
    for php_file in _find_php_files(root):
        try:
            content = php_file.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in route_patterns:
            if re.search(pattern, content):
                fw = _detect_framework_in_file(php_file)
                results.append(EntryPoint(path=php_file, framework=fw))
                break
    return results


def _detect_framework_in_file(php_file: Path) -> str:
    try:
        content = php_file.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return "php"
    for fw, indicators in _FRAMEWORK_DEPENDENCIES.items():
        for ind in indicators:
            if ind in content:
                return fw
    return "php"


def _scan_env_for_port(root: Path) -> int | None:
    env_file = root / ".env"
    if not env_file.exists():
        return None
    try:
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            m = re.match(r"(?:APP_PORT|PORT|SERVER_PORT)\s*=\s*(\d+)", line, re.IGNORECASE)
            if m:
                return int(m.group(1))
    except (OSError, UnicodeDecodeError):
        pass
    return None


def _scan_source_for_port(root: Path) -> int | None:
    patterns = [
        r":(\d+)\s*\)\s*->\s*run\s*\(",
        r"listen\s*\(\s*(\d+)",
        r"port\s*[=:]\s*(\d+)",
    ]
    for php_file in _find_php_files(root):
        try:
            content = php_file.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in patterns:
            m = re.search(pattern, content)
            if m:
                return int(m.group(1))
    return None


def _has_lock_file(root: Path) -> bool:
    return (root / "composer.lock").exists()


class PhpPlugin(LanguagePlugin):
    @property
    def name(self) -> str:
        return "php"

    def detect(self, root: Path) -> DetectionResult:
        if not root.exists():
            return DetectionResult(
                score=0.0,
                language="php",
                framework="",
                detail="Directory not found",
            )

        composer = _read_composer_json(root)
        if composer is None:
            php_files = _find_php_files(root)
            if not php_files:
                return DetectionResult(
                    score=0.0,
                    language="php",
                    framework="",
                    detail="No composer.json or .php files found",
                )
            return DetectionResult(
                score=0.3,
                language="php",
                framework="php",
                detail="PHP files found but no composer.json",
            )

        framework = _detect_framework_from_composer(composer)
        if not framework:
            frameworks_found = _scan_sources_for_framework(root)
            framework = _detect_framework_from_code(frameworks_found)

        entries = self.find_entry_points(root)
        score = 0.9 if entries else 0.7

        return DetectionResult(
            score=score,
            language="php",
            framework=framework or "php",
            entries=entries,
            build_tool="composer",
            detail=(
                f"PHP {framework or 'project'} with {len(entries)} entry point(s)"
                if entries
                else f"PHP {framework or 'project'}"
            ),
        )

    def find_entry_points(self, root: Path) -> list[EntryPoint]:
        entries = _find_entry_points_common(root)
        if entries:
            return entries

        entries = _find_entry_points_by_routes(root)
        if entries:
            return entries

        return []

    def detect_framework(self, root: Path, entry: EntryPoint) -> str:
        return entry.framework or "php"

    def instrument(
        self, entry: EntryPoint, service_name: str, otlp_endpoint: str = "http://localhost:4318"
    ) -> Instrumentation:
        return Instrumentation(
            env_vars={
                "OTEL_SERVICE_NAME": service_name,
                "OTEL_EXPORTER_OTLP_ENDPOINT": otlp_endpoint,
                "OTEL_PHP_AUTOLOAD_ENABLED": "true",
                "OTEL_METRICS_EXPORTER": "none",
                "OTEL_LOGS_EXPORTER": "none",
                "OTEL_TRACES_EXPORTER": "otlp",
            },
            files_to_install=[
                "open-telemetry/opentelemetry",
                "open-telemetry/opentelemetry-auto-php",
            ],
        )

    def run_command(self, entry: EntryPoint, port: int | None = None) -> list[str] | None:
        port = port or self.guess_port(entry) or 8080
        port_str = str(port)

        if entry.path.name == "artisan":
            return ["php", str(entry.path), "serve", f"--port={port_str}"]
        if entry.path.name == "console":
            return ["php", str(entry.path), "server:start", f"--port={port_str}"]

        if entry.path.name == "index.php":
            return ["php", "-S", f"0.0.0.0:{port_str}", "-t", str(entry.path.parent.parent / "public")]

        return ["php", "-S", f"0.0.0.0:{port_str}", str(entry.path)]

    def install_command(self, root: Path) -> list[str] | None:
        if _find_composer_json(root) is None:
            return None
        if _has_lock_file(root):
            return ["composer", "install", "--no-interaction", "--prefer-dist"]
        return ["composer", "install", "--no-interaction"]

    def guess_port(self, entry: EntryPoint) -> int | None:
        port = _scan_env_for_port(entry.path.parent)
        if port is not None:
            return port

        port = _scan_source_for_port(entry.path.parent)
        if port is not None:
            return port

        fw = entry.framework or "php"
        return _FRAMEWORK_PORTS.get(fw, 8080)

    def has_openapi(self) -> bool:
        return True

    def openapi_paths(self) -> list[str]:
        return [
            "/api/documentation",
            "/swagger.json",
            "/api/swagger.json",
            "/docs",
            "/openapi.json",
            "/api/docs",
        ]
