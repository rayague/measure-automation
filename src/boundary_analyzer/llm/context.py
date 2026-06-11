from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _safe_read(path: Path) -> str:
    """Read a file and return its content, or empty string on failure."""
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def _find_main_file(project_path: Path) -> Path | None:
    """Try to find the main application entry point."""
    candidates = [
        project_path / "app" / "main.py",
        project_path / "main.py",
        project_path / "src" / "main.py",
        project_path / "application" / "main.py",
        project_path / "app.py",
        project_path / "server.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _find_requirements(project_path: Path) -> Path | None:
    candidates = [
        project_path / "requirements.txt",
        project_path / "pyproject.toml",
        project_path / "setup.py",
        project_path / "Pipfile",
        project_path / "poetry.lock",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _detect_framework_from_files(project_path: Path) -> str:
    """Detect the web framework by scanning project files."""
    all_files = list(project_path.rglob("*"))
    filenames_lower = {f.name.lower() for f in all_files if f.is_file()}

    # Priority order: most specific first
    if "manage.py" in filenames_lower:
        return "django"

    # Check requirements.txt content
    req_path = _find_requirements(project_path)
    if req_path:
        content = _safe_read(req_path).lower()
        if "fastapi" in content:
            return "fastapi"
        if "flask" in content:
            return "flask"
        if "django" in content:
            return "django"
        if "starlette" in content:
            return "starlette"

    # Check imports in Python files
    for py_file in project_path.rglob("*.py"):
        content = _safe_read(py_file).lower()
        if "fastapi" in content:
            return "fastapi"
        if "flask" in content:
            return "flask"
        if "django" in content:
            return "django"

    return "unknown"


def _detect_orm(project_path: Path) -> str:
    """Detect ORM/database library."""
    req_path = _find_requirements(project_path)
    if req_path:
        content = _safe_read(req_path).lower()
        if "sqlalchemy" in content:
            return "sqlalchemy"
        if "sqlmodel" in content:
            return "sqlmodel"
        if "django" in content and req_path.name != "Pipfile":
            return "django-orm"
        if "tortoise-orm" in content:
            return "tortoise-orm"
    for py_file in project_path.rglob("*.py"):
        content = _safe_read(py_file).lower()
        if "sqlalchemy" in content:
            return "sqlalchemy"
        if "from django.db import models" in content:
            return "django-orm"
    return "unknown"


def _detect_http_client(project_path: Path) -> str:
    """Detect HTTP client used for inter-service calls."""
    for py_file in project_path.rglob("*.py"):
        content = _safe_read(py_file)
        if "import httpx" in content or "from httpx" in content:
            return "httpx"
        if "import requests" in content:
            return "requests"
        if "from aiohttp import" in content:
            return "aiohttp"
    return "unknown"


def _get_service_name(project_path: Path) -> str:
    """Derive a service name from the project folder name."""
    return project_path.name.strip().replace("_", "-").replace(" ", "-").lower()


def _scan_api_routes(project_path: Path) -> list[dict[str, str]]:
    """Try to extract API route definitions from the project."""
    routes: list[dict[str, str]] = []
    for py_file in sorted(project_path.rglob("*.py")):
        content = _safe_read(py_file)
        lines = content.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Match FastAPI/Flask-style route decorators
            if "@" in stripped and any(
                method in stripped.upper()
                for method in [".GET(", ".POST(", ".PUT(", ".DELETE(", ".PATCH("]
            ):
                routes.append({
                    "file": str(py_file.relative_to(project_path)),
                    "line": str(i + 1),
                    "route": stripped,
                })
            # Match router.api_route or router.add_api_route
            if ".api_route(" in stripped or ".add_api_route(" in stripped:
                routes.append({
                    "file": str(py_file.relative_to(project_path)),
                    "line": str(i + 1),
                    "route": stripped,
                })
    return routes


def build_project_context(project_path: Path) -> dict[str, Any]:
    """Build a complete context dictionary for a microservice project.

    This is the "web" the spider (LLM) sits in the middle of.
    It captures everything needed to understand and instrument a service.
    """
    main_file = _find_main_file(project_path)
    req_file = _find_requirements(project_path)

    context: dict[str, Any] = {
        "service_name": _get_service_name(project_path),
        "framework": _detect_framework_from_files(project_path),
        "orm": _detect_orm(project_path),
        "http_client": _detect_http_client(project_path),
        "project_path": str(project_path),
        "main_file": str(main_file.relative_to(project_path)) if main_file else None,
        "requirements_file": str(req_file.relative_to(project_path)) if req_file else None,
        "has_dockerfile": (project_path / "Dockerfile").exists(),
        "has_docker_compose": (project_path.parent / "docker-compose.yml").exists(),
        "structure": _scan_project_structure(project_path),
        "api_routes": _scan_api_routes(project_path),
        "main_content": _safe_read(main_file) if main_file else "",
        "requirements_content": _safe_read(req_file) if req_file else "",
    }

    return context


def _scan_project_structure(project_path: Path) -> list[str]:
    """Scan the project tree and return a readable directory listing."""
    entries: list[str] = []
    root = project_path.resolve()

    for path in sorted(root.rglob("*")):
        if path.is_file() and ".pyc" not in path.suffix and "__pycache__" not in str(path):
            try:
                relative = path.relative_to(root)
                entries.append(str(relative))
            except ValueError:
                pass

    return entries


def format_context_for_prompt(context: dict[str, Any]) -> str:
    """Format the project context dict into a readable text block for the LLM."""
    lines = [
        f"Service name: {context['service_name']}",
        f"Framework: {context['framework']}",
        f"ORM: {context['orm']}",
        f"HTTP client: {context['http_client']}",
        f"Main file: {context['main_file']}",
        f"Requirements file: {context['requirements_file']}",
        f"Has Dockerfile: {context['has_dockerfile']}",
        "",
        "--- Project Structure ---",
    ]
    for entry in context.get("structure", []):
        lines.append(f"  {entry}")

    routes = context.get("api_routes", [])
    if routes:
        lines.append("")
        lines.append("--- API Routes Detected ---")
        for route in routes:
            lines.append(f"  {route['file']}:{route['line']}  {route['route']}")

    lines.append("")
    lines.append("--- Main Application Code ---")
    lines.append(context.get("main_content", "(not found)"))

    req_content = context.get("requirements_content", "")
    if req_content:
        req_file = context.get("requirements_file", "requirements.txt")
        lines.append("")
        lines.append(f"--- {req_file} ---")
        lines.append(req_content)

    return "\n".join(lines)
