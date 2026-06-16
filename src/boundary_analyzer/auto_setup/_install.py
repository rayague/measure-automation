from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from boundary_analyzer.auto_setup._detect import IS_WINDOWS, SUPPORTED_FRAMEWORKS

"""Package installation for auto-setup: pip, npm, and Composer installers."""

logger = logging.getLogger(__name__)

PYTHON_BASE_PACKAGES = [
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-grpc",
    "opentelemetry-instrumentation",
]

FRAMEWORK_PACKAGES = {
    "flask": ["opentelemetry-instrumentation-flask", "opentelemetry-instrumentation-sqlalchemy"],
    "fastapi": ["opentelemetry-instrumentation-fastapi", "opentelemetry-instrumentation-sqlalchemy"],
    "django": ["opentelemetry-instrumentation-django", "opentelemetry-instrumentation-sqlalchemy"],
    "djangorest": ["opentelemetry-instrumentation-django", "opentelemetry-instrumentation-sqlalchemy"],
    "starlette": ["opentelemetry-instrumentation-starlette", "opentelemetry-instrumentation-sqlalchemy"],
    "tornado": ["opentelemetry-instrumentation-tornado"],
    "laravel": [],
    "express": [],
    "nextjs": [],
    "nestjs": [],
}

JS_NPM_PACKAGES = [
    "@opentelemetry/sdk-node",
    "@opentelemetry/api",
    "@opentelemetry/auto-instrumentations-node",
    "@opentelemetry/exporter-trace-otlp-grpc",
    "@grpc/grpc-js",
]

NESTJS_EXTRA_PACKAGES = [
    "@opentelemetry/instrumentation-http",
    "@opentelemetry/instrumentation-express",
]

LARAVEL_COMPOSER_PACKAGES = [
    "open-telemetry/sdk",
    "open-telemetry/exporter-otlp-grpc",
    "open-telemetry/opentelemetry-auto-laravel",
]


def install_packages(framework: str, project_path: Path) -> None:
    """Install OpenTelemetry packages for the detected framework (pip/npm/Composer)."""
    lang = SUPPORTED_FRAMEWORKS[framework]["lang"]

    if lang == "python":
        _pip_install(PYTHON_BASE_PACKAGES + FRAMEWORK_PACKAGES.get(framework, []))
    elif lang == "js":
        pkgs = JS_NPM_PACKAGES[:]
        if framework == "nestjs":
            pkgs += NESTJS_EXTRA_PACKAGES
        _npm_install(pkgs, project_path)
    elif lang == "php":
        _composer_install(LARAVEL_COMPOSER_PACKAGES, project_path)


def _pip_install(packages: list) -> None:
    """Install Python packages via pip."""
    logger.info("Installing Python packages: %s", ", ".join(packages))
    cmd = [sys.executable, "-m", "pip", "install", "--quiet"] + packages
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        logger.error("pip install failed:\n%s", result.stderr)
        sys.exit(1)
    logger.info("Python packages installed.")


def _npm_install(packages: list, project_path: Path) -> None:
    """Install Node.js packages via npm."""
    logger.info("Installing Node.js packages: %s", ", ".join(packages))
    npm = "npm.cmd" if IS_WINDOWS else "npm"
    cmd = [npm, "install", "--save"] + packages
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=project_path)
    if result.returncode != 0:
        logger.error("npm install failed:\n%s", result.stderr)
        sys.exit(1)
    logger.info("Node.js packages installed.")


def _composer_install(packages: list, project_path: Path) -> None:
    """Install PHP packages via Composer."""
    logger.info("Installing PHP packages via Composer: %s", ", ".join(packages))
    composer = "composer.bat" if IS_WINDOWS else "composer"
    cmd = [composer, "require"] + packages
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=project_path)
    if result.returncode != 0:
        logger.error("composer require failed:\n%s", result.stderr)
        sys.exit(1)
    logger.info("PHP packages installed.")
