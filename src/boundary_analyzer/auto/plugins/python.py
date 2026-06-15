from __future__ import annotations

import ast
import logging
import re
import sys
from pathlib import Path

from boundary_analyzer.auto.models import DetectionResult, EntryPoint
from boundary_analyzer.auto.plugins.base import Instrumentation, LanguagePlugin

logger = logging.getLogger(__name__)


_COMMON_ENTRY_NAMES = [
    "main.py",
    "app.py",
    "run.py",
    "server.py",
    "manage.py",
    "application.py",
    "wsgi.py",
    "api.py",
]

_FRAMEWORK_IMPORTS: dict[str, list[str]] = {
    "fastapi": ["fastapi", "uvicorn"],
    "flask": ["flask"],
    "django": ["django"],
    "starlette": ["starlette"],
    "tornado": ["tornado"],
    "aiohttp": ["aiohttp"],
}

_FRAMEWORK_PORTS: dict[str, int] = {
    "fastapi": 8000,
    "flask": 5000,
    "django": 8000,
    "starlette": 8000,
    "tornado": 8888,
    "aiohttp": 8080,
}

_FRAMEWORK_RUN_COMMANDS: dict[str, list[str]] = {
    "fastapi": ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "{port}"],
    "flask": ["flask", "run", "--host=0.0.0.0", "--port={port}"],
    "django": ["python", "manage.py", "runserver", "0.0.0.0:{port}"],
}

_HEALTH_KEYWORDS = frozenset(
    {
        "health",
        "healthz",
        "readyz",
        "livez",
        "ready",
        "metrics",
        "favicon.ico",
    }
)


def _list_py_files(root: Path) -> list[Path]:
    return list(root.rglob("*.py"))


def _find_build_files(root: Path) -> list[Path]:
    files = []
    for name in ["requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile"]:
        p = root / name
        if p.exists():
            files.append(p)
    return files


def _scan_imports(root: Path) -> dict[str, set[str]]:
    frameworks_found: dict[str, set[str]] = {}
    for py_file in _list_py_files(root):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    _check_import(alias.name, frameworks_found, py_file)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    _check_import(node.module, frameworks_found, py_file)
    return frameworks_found


def _check_import(module: str, found: dict[str, set[str]], source: Path):
    for framework, imports in _FRAMEWORK_IMPORTS.items():
        for imp in imports:
            if module == imp or module.startswith(f"{imp}."):
                found.setdefault(framework, set()).add(str(source))
                return


def _find_main_blocks(py_files: list[Path]) -> list[Path]:
    results = []
    for py_file in py_files:
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                if (
                    isinstance(node.test, ast.Compare)
                    and isinstance(node.test.left, ast.Name)
                    and node.test.left.id == "__name__"
                    and isinstance(node.test.ops, list)
                    and len(node.test.ops) == 1
                    and isinstance(node.test.ops[0], ast.Eq)
                    and isinstance(node.test.comparators, list)
                    and len(node.test.comparators) == 1
                    and isinstance(node.test.comparators[0], ast.Constant)
                    and node.test.comparators[0].value == "__main__"
                ):
                    results.append(py_file)
                    break
    return results


def _scan_config_for_port(root: Path) -> int | None:
    env_file = root / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            m = re.match(r"(?:PORT|APP_PORT|SERVER_PORT|HOST_PORT)\s*=\s*(\d+)", line, re.IGNORECASE)
            if m:
                return int(m.group(1))

    config_py = root / "config.py"
    if config_py.exists():
        for line in config_py.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"(?:PORT|APP_PORT)\s*[=:]\s*(\d+)", line, re.IGNORECASE)
            if m:
                return int(m.group(1))

    return None


def _detect_framework_from_code(frameworks_found: dict[str, set[str]]) -> str:
    priority = ["fastapi", "flask", "django", "starlette", "tornado", "aiohttp"]
    for fw in priority:
        if fw in frameworks_found:
            return fw
    return "python"


def _find_app_variable(py_file: Path, framework: str) -> str | None:
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None

    if framework == "fastapi":
        target_class = "FastAPI"
    elif framework == "flask":
        target_class = "Flask"
    elif framework == "django":
        return "application"
    else:
        target_class = None

    if not target_class:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if (
                        isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Name)
                        and node.value.func.id == target_class
                    ):
                        return target.id
    return None


def _check_is_python_file(path: Path) -> bool:
    if path.suffix != ".py":
        return False
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        ast.parse(content)
        return True
    except (SyntaxError, UnicodeDecodeError):
        return False


class PythonPlugin(LanguagePlugin):
    @property
    def name(self) -> str:
        return "python"

    def detect(self, root: Path) -> DetectionResult:
        if not root.exists():
            return DetectionResult(score=0.0, language="python", framework="", detail="Directory not found")

        build_files = _find_build_files(root)
        if not build_files:
            py_files = _list_py_files(root)
            if not py_files:
                return DetectionResult(
                    score=0.0, language="python", framework="", detail="No Python build files or .py files found"
                )
            return DetectionResult(
                score=0.3,
                language="python",
                framework="python",
                detail="Python files found but no standard build file",
            )

        frameworks_found = _scan_imports(root)
        framework = _detect_framework_from_code(frameworks_found)

        build_tool = (
            build_files[0]
            .name.replace("requirements.txt", "pip")
            .replace("pyproject.toml", "poetry")
            .replace("setup.py", "setuptools")
            .replace("Pipfile", "pipenv")
            .replace("setup.cfg", "setuptools")
        )

        entries = self.find_entry_points(root)
        score = 0.9 if entries else 0.7

        return DetectionResult(
            score=score,
            language="python",
            framework=framework,
            entries=entries,
            build_tool=build_tool,
            detail=f"Python {framework} project with {len(entries)} entry point(s)"
            if entries
            else f"Python {framework} project (no entry point auto-detected)",
        )

    def find_entry_points(self, root: Path) -> list[EntryPoint]:
        py_files = _list_py_files(root)
        if not py_files:
            return []

        candidates: list[EntryPoint] = []

        for name in _COMMON_ENTRY_NAMES:
            p = root / name
            if p.exists() and _check_is_python_file(p):
                fw = self._detect_framework_in_file(p)
                candidates.append(EntryPoint(path=p, framework=fw))

        if candidates:
            return candidates

        main_blocks = _find_main_blocks(py_files)
        for p in main_blocks:
            if p not in [c.path for c in candidates]:
                fw = self._detect_framework_in_file(p)
                candidates.append(EntryPoint(path=p, framework=fw))

        if candidates:
            return candidates

        frameworks_found = _scan_imports(root)
        framework = _detect_framework_from_code(frameworks_found)
        if framework != "python" and py_files:
            fw_files = frameworks_found.get(framework, set())
            for f in fw_files:
                p = Path(f)
                if p.exists() and p not in [c.path for c in candidates]:
                    candidates.append(EntryPoint(path=p, framework=framework))
            if not candidates:
                candidates.append(EntryPoint(path=py_files[0], framework=framework))

        return candidates

    def detect_framework(self, root: Path, entry: EntryPoint) -> str:
        return entry.framework

    def _detect_framework_in_file(self, py_file: Path) -> str:
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
            for fw, imports in _FRAMEWORK_IMPORTS.items():
                for imp in imports:
                    if f"import {imp}" in content or f"from {imp}" in content:
                        return fw
        except (OSError, UnicodeDecodeError):
            pass
        return "python"

    def instrument(
        self, entry: EntryPoint, service_name: str, otlp_endpoint: str = "http://localhost:4318"
    ) -> Instrumentation:
        return Instrumentation(
            env_vars={
                "OTEL_SERVICE_NAME": service_name,
                "OTEL_EXPORTER_OTLP_ENDPOINT": otlp_endpoint,
                "OTEL_PYTHON_CONFIGURATOR": "opentelemetry-sdk-configurator",
                "OTEL_METRICS_EXPORTER": "none",
                "OTEL_LOGS_EXPORTER": "none",
            },
            files_to_install=[
                "opentelemetry-sdk",
                "opentelemetry-exporter-otlp",
                "opentelemetry-instrumentation",
            ],
        )

    def run_command(self, entry: EntryPoint, port: int | None = None) -> list[str]:
        fw = entry.framework or self._detect_framework_in_file(entry.path)
        port = port or self.guess_port(entry) or 8000
        port_str = str(port)

        if fw == "fastapi":
            app_var = _find_app_variable(entry.path, fw) or "app"
            module = entry.path.stem
            return [
                sys.executable,
                "-m",
                "uvicorn",
                f"{module}:{app_var}",
                "--host",
                "0.0.0.0",
                "--port",
                port_str,
            ]

        if fw == "flask":
            return [
                sys.executable,
                "-m",
                "flask",
                "run",
                "--host=0.0.0.0",
                f"--port={port_str}",
            ]

        if fw == "django":
            if entry.path.name == "manage.py":
                return [sys.executable, str(entry.path), "runserver", f"0.0.0.0:{port_str}"]
            return [sys.executable, "manage.py", "runserver", f"0.0.0.0:{port_str}"]

        if entry.path.name == "manage.py":
            return [sys.executable, str(entry.path), "runserver", f"0.0.0.0:{port_str}"]

        return [sys.executable, str(entry.path)]

    def install_command(self, root: Path) -> list[str] | None:
        req_file = root / "requirements.txt"
        if req_file.exists():
            return [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                str(req_file),
            ]

        pipfile = root / "Pipfile"
        if pipfile.exists():
            return [sys.executable, "-m", "pipenv", "install"]

        return None

    def guess_port(self, entry: EntryPoint) -> int | None:
        config_port = _scan_config_for_port(entry.path.parent)
        if config_port:
            return config_port

        fw = entry.framework or self._detect_framework_in_file(entry.path)
        return _FRAMEWORK_PORTS.get(fw)

    def has_openapi(self) -> bool:
        return True

    def openapi_paths(self) -> list[str]:
        return [
            "/openapi.json",
            "/docs",
            "/swagger.json",
            "/swagger/v1/swagger.json",
            "/api/openapi.json",
        ]
