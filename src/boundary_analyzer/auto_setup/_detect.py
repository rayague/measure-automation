from __future__ import annotations

import logging
import platform
from pathlib import Path

"""Framework detection for auto-setup: identify the web framework used by a project."""

logger = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"
JAEGER_GRPC_PORT = 4317
JAEGER_UI_PORT = 16686

SUPPORTED_FRAMEWORKS = {
    "flask": {"lang": "python", "display": "Flask"},
    "fastapi": {"lang": "python", "display": "FastAPI"},
    "django": {"lang": "python", "display": "Django"},
    "djangorest": {"lang": "python", "display": "Django REST Framework"},
    "starlette": {"lang": "python", "display": "Starlette"},
    "tornado": {"lang": "python", "display": "Tornado"},
    "laravel": {"lang": "php", "display": "Laravel"},
    "express": {"lang": "js", "display": "Express.js"},
    "nextjs": {"lang": "js", "display": "Next.js"},
    "nestjs": {"lang": "js", "display": "Nest.js"},
}


def detect_framework(project_path: Path) -> str:
    """Auto-detect the web framework used by a project (FastAPI, Flask, Django, etc.)."""
    files = list(project_path.rglob("*"))
    filenames = {f.name.lower() for f in files if f.is_file()}
    all_text = _read_project_text(project_path, max_files=30)

    if "artisan" in filenames or "composer.json" in filenames:
        composer = project_path / "composer.json"
        if composer.exists() and "laravel" in composer.read_text(errors="ignore").lower():
            return "laravel"

    pkg = project_path / "package.json"
    if pkg.exists():
        pkg_text = pkg.read_text(errors="ignore").lower()
        if "@nestjs/core" in pkg_text:
            return "nestjs"
        if "next" in pkg_text and '"next"' in pkg_text:
            return "nextjs"
        if "express" in pkg_text:
            return "express"

    req_files = ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile"]
    req_text = ""
    for rf in req_files:
        f = project_path / rf
        if f.exists():
            req_text += f.read_text(errors="ignore").lower()

    combined = req_text + all_text.lower()

    if "fastapi" in combined:
        return "fastapi"
    if "djangorestframework" in combined or "rest_framework" in combined:
        return "djangorest"
    if "django" in combined:
        return "django"
    if "starlette" in combined:
        return "starlette"
    if "tornado" in combined:
        return "tornado"
    if "flask" in combined:
        return "flask"

    return "unknown"


def _read_project_text(project_path: Path, max_files: int = 30) -> str:
    """Read text from project source files for framework detection heuristics."""
    extensions = {".py", ".js", ".ts", ".php", ".json"}
    skip_dirs = {"node_modules", ".git", "vendor", "__pycache__", ".next", "dist"}
    text = ""
    count = 0

    for f in project_path.rglob("*"):
        if count >= max_files:
            break
        if any(part in skip_dirs for part in f.parts):
            continue
        if f.suffix.lower() in extensions and f.is_file():
            try:
                text += f.read_text(errors="ignore")
                count += 1
            except (OSError, PermissionError):
                logger.warning("Failed to read file: %s", f)

    return text
