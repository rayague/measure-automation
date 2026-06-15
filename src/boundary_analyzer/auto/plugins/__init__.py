from __future__ import annotations

import logging
from pathlib import Path

from boundary_analyzer.auto.errors import AnalysisError, ErrorCode
from boundary_analyzer.auto.models import DetectionResult
from boundary_analyzer.auto.plugins.base import LanguagePlugin
from boundary_analyzer.auto.plugins.dotnet import DotNetPlugin
from boundary_analyzer.auto.plugins.java import JavaPlugin
from boundary_analyzer.auto.plugins.node import NodePlugin
from boundary_analyzer.auto.plugins.php import PhpPlugin
from boundary_analyzer.auto.plugins.python import PythonPlugin

logger = logging.getLogger(__name__)

_PLUGINS: list[LanguagePlugin] = []
_DISCOVERED: dict[str, LanguagePlugin] = {}


def _ensure_loaded():
    if not _PLUGINS:
        register(PythonPlugin())
        register(JavaPlugin())
        register(NodePlugin())
        register(PhpPlugin())
        register(DotNetPlugin())


def register(plugin: LanguagePlugin) -> None:
    _PLUGINS.append(plugin)


def detect_language(root: Path) -> tuple[LanguagePlugin, DetectionResult]:
    _ensure_loaded()

    candidates: list[tuple[LanguagePlugin, DetectionResult]] = []
    for plugin in _PLUGINS:
        result = plugin.detect(root)
        if result.score >= 0.3:
            candidates.append((plugin, result))

    if not candidates:
        found_files = ", ".join(sorted(p.name for p in root.iterdir() if p.is_file()))
        raise AnalysisError(
            code=ErrorCode.LANG_NOT_FOUND,
            scope=str(root),
            _override_detail=(
                f"Found files: {found_files}\n"
                f"Expected one of:\n"
                f"  Python:  requirements.txt, pyproject.toml, setup.py, Pipfile\n"
                f"  Java:    pom.xml, build.gradle, build.gradle.kts\n"
                f"  Node.js: package.json, package-lock.json\n"
                f"  .NET:    *.csproj, *.sln\n"
                f"  PHP:     composer.json"
            ),
            recoverable=False,
        )

    best = max(candidates, key=lambda c: c[1].score)
    _DISCOVERED[str(root.resolve())] = best[0]
    return best


def get_plugin_for_project(root: Path) -> LanguagePlugin | None:
    return _DISCOVERED.get(str(root.resolve()))


def list_supported_languages() -> list[str]:
    _ensure_loaded()
    return [p.name for p in _PLUGINS]
