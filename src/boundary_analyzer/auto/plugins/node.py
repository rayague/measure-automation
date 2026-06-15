from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from boundary_analyzer.auto.models import DetectionResult, EntryPoint
from boundary_analyzer.auto.plugins.base import Instrumentation, LanguagePlugin

logger = logging.getLogger(__name__)


_JS_ENTRY_NAMES = [
    "index.js",
    "server.js",
    "app.js",
    "main.js",
    "index.ts",
    "server.ts",
    "app.ts",
    "main.ts",
]

_FRAMEWORK_DEPENDENCIES: dict[str, list[str]] = {
    "express": ["express"],
    "fastify": ["fastify"],
    "nestjs": ["@nestjs/core"],
    "koa": ["koa"],
}

_FRAMEWORK_PORTS: dict[str, int] = {
    "express": 3000,
    "fastify": 3000,
    "nestjs": 3001,
    "koa": 3000,
    "node": 3000,
}

_NODE_OTEL_PACKAGES = [
    "@opentelemetry/sdk-node",
    "@opentelemetry/auto-instrumentations-node",
    "@opentelemetry/exporter-otlp-http",
]


def _find_package_json(root: Path) -> Path | None:
    p = root / "package.json"
    return p if p.exists() else None


def _read_package_json(root: Path) -> dict | None:
    pkg = _find_package_json(root)
    if pkg is None:
        return None
    try:
        return json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None


def _detect_framework_from_deps(pkg: dict) -> str:
    all_deps: dict[str, str] = {}
    for section in ["dependencies", "devDependencies", "peerDependencies"]:
        deps = pkg.get(section, {})
        if isinstance(deps, dict):
            all_deps.update(deps)

    priority = ["express", "fastify", "nestjs", "koa"]
    for fw in priority:
        indicators = _FRAMEWORK_DEPENDENCIES[fw]
        for ind in indicators:
            if ind in all_deps:
                return fw
    return ""


def _find_js_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in ["*.js", "*.mjs", "*.cjs"]:
        files.extend(root.rglob(pattern))
    return sorted(files)


def _find_ts_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.ts"))


def _is_typescript_project(root: Path) -> bool:
    return (root / "tsconfig.json").exists() or len(_find_ts_files(root)) > 0


def _scan_sources_for_framework(root: Path) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for js_file in _find_js_files(root):
        try:
            content = js_file.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for fw, indicators in _FRAMEWORK_DEPENDENCIES.items():
            for ind in indicators:
                if (
                    f"require('{ind}')" in content
                    or f'require("{ind}")' in content
                    or f"from '{ind}'" in content
                    or f'from "{ind}"' in content
                ):
                    found.setdefault(fw, set()).add(str(js_file))
    for ts_file in _find_ts_files(root):
        try:
            content = ts_file.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for fw, indicators in _FRAMEWORK_DEPENDENCIES.items():
            for ind in indicators:
                if f"from '{ind}'" in content or f'from "{ind}"' in content or f"require('{ind}')" in content:
                    found.setdefault(fw, set()).add(str(ts_file))
    return found


def _detect_framework_from_code(frameworks_found: dict[str, set[str]]) -> str:
    priority = ["express", "fastify", "nestjs", "koa"]
    for fw in priority:
        if fw in frameworks_found:
            return fw
    return "node"


def _find_entry_points_from_package_json(pkg: dict, root: Path) -> list[EntryPoint]:
    entries: list[EntryPoint] = []
    main_field = pkg.get("main", "")
    if main_field:
        main_path = root / main_field
        if main_path.exists():
            fw = _detect_framework_in_file(main_path)
            entries.append(EntryPoint(path=main_path, framework=fw))
    return entries


def _find_entry_points_common_names(root: Path) -> list[EntryPoint]:
    candidates: list[EntryPoint] = []
    for name in _JS_ENTRY_NAMES:
        p = root / name
        if p.exists():
            fw = _detect_framework_in_file(p)
            candidates.append(EntryPoint(path=p, framework=fw))
    return candidates


def _find_entry_points_listen_pattern(root: Path) -> list[EntryPoint]:
    results: list[EntryPoint] = []
    listen_patterns = [
        r"\.listen\s*\(",
        r"\.listenAsync\s*\(",
    ]
    for js_file in _find_js_files(root):
        try:
            content = js_file.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in listen_patterns:
            if re.search(pattern, content):
                fw = _detect_framework_in_file(js_file)
                results.append(EntryPoint(path=js_file, framework=fw))
                break
    return results


def _detect_framework_in_file(js_file: Path) -> str:
    try:
        content = js_file.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return "node"
    for fw, indicators in _FRAMEWORK_DEPENDENCIES.items():
        for ind in indicators:
            if ind in content:
                return fw
    return "node"


def _find_npm_script(pkg: dict, script_name: str = "start") -> str | None:
    scripts = pkg.get("scripts", {})
    if isinstance(scripts, dict) and script_name in scripts:
        return scripts[script_name]
    return None


def _scan_env_for_port(root: Path) -> int | None:
    env_file = root / ".env"
    if not env_file.exists():
        return None
    try:
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            m = re.match(r"(?:PORT|SERVER_PORT|APP_PORT|NODE_PORT)\s*=\s*(\d+)", line, re.IGNORECASE)
            if m:
                return int(m.group(1))
    except (OSError, UnicodeDecodeError):
        pass
    return None


def _scan_source_for_port(root: Path) -> int | None:
    port_patterns = [
        r"app\.listen\s*\(\s*(\d+)",
        r"server\.listen\s*\(\s*(\d+)",
        r"fastify\.listen\s*\(\s*\{\s*port\s*:\s*(\d+)",
        r"process\.env\.PORT\s*\|\|\s*(\d+)",
        r"process\.env\.PORT\s*\?\?\s*(\d+)",
        r"port\s*[=:]\s*(\d+)",
    ]
    for js_file in _find_js_files(root):
        try:
            content = js_file.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in port_patterns:
            m = re.search(pattern, content)
            if m:
                return int(m.group(1))
    return None


def _has_npm_lock(root: Path) -> bool:
    for name in ["package-lock.json", "yarn.lock", "pnpm-lock.yaml"]:
        if (root / name).exists():
            return True
    return False


class NodePlugin(LanguagePlugin):
    @property
    def name(self) -> str:
        return "node"

    def detect(self, root: Path) -> DetectionResult:
        if not root.exists():
            return DetectionResult(
                score=0.0,
                language="node",
                framework="",
                detail="Directory not found",
            )

        pkg = _read_package_json(root)
        if pkg is None:
            js_files = _find_js_files(root)
            if not js_files:
                return DetectionResult(
                    score=0.0,
                    language="node",
                    framework="",
                    detail="No package.json or .js files found",
                )
            return DetectionResult(
                score=0.3,
                language="node",
                framework="node",
                detail="JavaScript files found but no package.json",
            )

        framework = _detect_framework_from_deps(pkg)
        if not framework:
            frameworks_found = _scan_sources_for_framework(root)
            framework = _detect_framework_from_code(frameworks_found)

        entries = self.find_entry_points(root)
        score = 0.9 if entries else 0.7

        return DetectionResult(
            score=score,
            language="node",
            framework=framework or "node",
            entries=entries,
            build_tool="npm",
            detail=(
                f"Node.js {framework or 'project'} with {len(entries)} entry point(s)"
                if entries
                else f"Node.js {framework or 'project'}"
            ),
        )

    def find_entry_points(self, root: Path) -> list[EntryPoint]:
        pkg = _read_package_json(root)

        if pkg:
            entries = _find_entry_points_from_package_json(pkg, root)
            if entries:
                return entries

        entries = _find_entry_points_common_names(root)
        if entries:
            return entries

        if pkg:
            start_script = _find_npm_script(pkg, "start")
            if start_script:
                m = re.search(r"(?:node|ts-node|tsx)\s+(\S+)", start_script)
                if m:
                    script_path = root / m.group(1)
                    if script_path.exists():
                        fw = _detect_framework_in_file(script_path)
                        return [EntryPoint(path=script_path, framework=fw)]

        entries = _find_entry_points_listen_pattern(root)
        if entries:
            return entries

        return []

    def detect_framework(self, root: Path, entry: EntryPoint) -> str:
        return entry.framework or "node"

    def instrument(
        self, entry: EntryPoint, service_name: str, otlp_endpoint: str = "http://localhost:4318"
    ) -> Instrumentation:
        return Instrumentation(
            env_vars={
                "OTEL_SERVICE_NAME": service_name,
                "OTEL_EXPORTER_OTLP_ENDPOINT": otlp_endpoint,
                "OTEL_METRICS_EXPORTER": "none",
                "OTEL_LOGS_EXPORTER": "none",
                "NODE_OPTIONS": "--require @opentelemetry/auto-instrumentations-node/register",
            },
            files_to_install=_NODE_OTEL_PACKAGES,
        )

    def run_command(self, entry: EntryPoint, port: int | None = None) -> list[str] | None:
        root = entry.path.parent

        pkg = _read_package_json(root)
        if pkg:
            start_script = _find_npm_script(pkg, "start")
            if start_script:
                return ["npm", "start"]

        is_ts = entry.path.suffix == ".ts"
        runner = "tsx" if is_ts else "node"
        return [runner, str(entry.path)]

    def install_command(self, root: Path) -> list[str] | None:
        pkg = _find_package_json(root)
        if pkg is None:
            return None

        if _has_npm_lock(root):
            return ["npm", "ci"]

        return ["npm", "install"]

    def guess_port(self, entry: EntryPoint) -> int | None:
        port = _scan_env_for_port(entry.path.parent)
        if port is not None:
            return port

        port = _scan_source_for_port(entry.path.parent)
        if port is not None:
            return port

        fw = entry.framework or "node"
        return _FRAMEWORK_PORTS.get(fw, 3000)

    def has_openapi(self) -> bool:
        return True

    def openapi_paths(self) -> list[str]:
        return [
            "/api-docs",
            "/swagger.json",
            "/api/swagger.json",
            "/docs",
            "/openapi.json",
            "/api/documentation",
        ]
