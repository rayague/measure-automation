from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _safe_read(path: Path) -> str:
    """Read a file and return its content, or empty string on failure."""
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


_LANG_DETECT = {
    "python": {
        "main": ["main.py", "app.py", "server.py", "run.py", "manage.py", "wsgi.py", "api.py"],
        "deps": ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile", "poetry.lock"],
        "ext": ".py",
    },
    "node": {
        "main": ["index.js", "app.js", "server.js", "main.js", "index.ts", "app.ts", "server.ts"],
        "deps": ["package.json", "package-lock.json", "yarn.lock"],
        "ext": ".js,.jsx,.ts,.tsx",
    },
    "java": {
        "main": ["src/main/java/**/Application.java", "src/main/java/**/Main.java"],
        "deps": ["pom.xml", "build.gradle", "build.gradle.kts"],
        "ext": ".java",
    },
    "go": {
        "main": ["main.go", "cmd/main.go", "cmd/*/main.go"],
        "deps": ["go.mod", "go.sum"],
        "ext": ".go",
    },
    "rust": {
        "main": ["src/main.rs"],
        "deps": ["Cargo.toml", "Cargo.lock"],
        "ext": ".rs",
    },
}

def _detect_language(project_path: Path) -> str:
    """Detect project language by checking for characteristic files."""
    for _file in project_path.iterdir():
        name = _file.name.lower()
        if name == "package.json":
            return "node"
        if name in ("pom.xml", "build.gradle", "build.gradle.kts"):
            return "java"
        if name == "go.mod":
            return "go"
        if name == "cargo.toml":
            return "rust"
        if name in ("requirements.txt", "setup.py", "pyproject.toml"):
            return "python"
    return "python"  # default


def _find_main_file(project_path: Path, lang: str = "") -> Path | None:
    """Try to find the main application entry point for the detected language."""
    if not lang:
        lang = _detect_language(project_path)
    names = _LANG_DETECT.get(lang, _LANG_DETECT["python"])["main"]

    if lang == "java":
        for pattern in names:
            matches = sorted(project_path.glob(pattern))
            if matches:
                return matches[0]
        return None

    # Check known subdirectories first
    for sub in ["app", "src", "application"]:
        for name in names:
            c = project_path / sub / name
            if c.exists():
                return c
    # Then check root
    for name in names:
        c = project_path / name
        if c.exists():
            return c
    return None


def _find_deps_file(project_path: Path, lang: str = "") -> Path | None:
    """Find the dependency file for the detected language."""
    if not lang:
        lang = _detect_language(project_path)
    candidates = _LANG_DETECT.get(lang, _LANG_DETECT["python"])["deps"]
    for name in candidates:
        c = project_path / name
        if c.exists():
            return c
    return None


def _detect_framework_from_files(project_path: Path, lang: str = "") -> str:
    """Detect the web framework by scanning project files."""
    if not lang:
        lang = _detect_language(project_path)
    all_files = list(project_path.rglob("*"))
    filenames_lower = {f.name.lower() for f in all_files if f.is_file()}

    if lang == "python":
        if "manage.py" in filenames_lower:
            return "django"
        deps_path = _find_deps_file(project_path, "python")
        if deps_path:
            content = _safe_read(deps_path).lower()
            if "fastapi" in content:
                return "fastapi"
            if "flask" in content:
                return "flask"
            if "django" in content:
                return "django"
            if "starlette" in content:
                return "starlette"
        for py_file in project_path.rglob("*.py"):
            content = _safe_read(py_file).lower()
            if "fastapi" in content:
                return "fastapi"
            if "flask" in content:
                return "flask"
            if "django" in content:
                return "django"
        return "unknown"

    if lang == "node":
        pkg = project_path / "package.json"
        if pkg.exists():
            content = _safe_read(pkg)
            for fw in ["express", "fastify", "nestjs", "koa", "hapi"]:
                if fw in content.lower():
                    return fw
        return "node-unknown"

    if lang == "java":
        for f in ["pom.xml", "build.gradle", "build.gradle.kts"]:
            p = project_path / f
            if p.exists():
                content = _safe_read(p).lower()
                for fw in ["spring-boot", "quarkus", "micronaut", "helidon"]:
                    if fw in content:
                        return fw
        return "java-unknown"

    if lang == "go":
        mod = project_path / "go.mod"
        if mod.exists():
            content = _safe_read(mod).lower()
            for fw in ["gin", "echo", "fiber", "chi", "gorilla/mux", "net/http"]:
                if fw in content:
                    return fw
        return "go-unknown"

    return "unknown"


def _detect_orm(project_path: Path, lang: str = "") -> str:
    """Detect ORM/database library."""
    if not lang:
        lang = _detect_language(project_path)

    if lang == "python":
        deps_path = _find_deps_file(project_path, "python")
        if deps_path:
            content = _safe_read(deps_path).lower()
            if "sqlalchemy" in content:
                return "sqlalchemy"
            if "sqlmodel" in content:
                return "sqlmodel"
            if "django" in content and deps_path.name != "Pipfile":
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

    if lang == "node":
        pkg = project_path / "package.json"
        if pkg.exists():
            content = _safe_read(pkg).lower()
            for orm in ["sequelize", "typeorm", "prisma", "mongoose", "knex"]:
                if orm in content:
                    return orm
        return "unknown"

    if lang == "java":
        for f in ["pom.xml", "build.gradle", "build.gradle.kts"]:
            p = project_path / f
            if p.exists():
                content = _safe_read(p).lower()
                for orm in ["hibernate", "mybatis", "jooq", "spring-data"]:
                    if orm in content:
                        return orm
        return "unknown"

    if lang == "go":
        mod = project_path / "go.mod"
        if mod.exists():
            content = _safe_read(mod).lower()
            for orm in ["gorm", "ent", "sqlx", "sqlc"]:
                if orm in content:
                    return orm
        return "unknown"

    return "unknown"


def _detect_http_client(project_path: Path, lang: str = "") -> str:
    """Detect HTTP client used for inter-service calls."""
    if not lang:
        lang = _detect_language(project_path)

    if lang == "python":
        for py_file in project_path.rglob("*.py"):
            content = _safe_read(py_file)
            if "import httpx" in content or "from httpx" in content:
                return "httpx"
            if "import requests" in content:
                return "requests"
            if "from aiohttp import" in content:
                return "aiohttp"
        return "unknown"

    if lang == "node":
        pkg = project_path / "package.json"
        if pkg.exists():
            content = _safe_read(pkg).lower()
            for client in ["axios", "got", "node-fetch", "undici", "superagent"]:
                if client in content:
                    return client
        return "unknown"

    return "unknown"


def _get_service_name(project_path: Path) -> str:
    """Derive a service name from the project folder name."""
    return project_path.name.strip().replace("_", "-").replace(" ", "-").lower()


def _scan_api_routes(project_path: Path, lang: str = "") -> list[dict[str, str]]:
    """Try to extract API route definitions from the project."""
    routes: list[dict[str, str]] = []
    if not lang:
        lang = _detect_language(project_path)

    if lang == "python":
        for py_file in sorted(project_path.rglob("*.py")):
            content = _safe_read(py_file)
            lines = content.split("\n")
            for i, line in enumerate(lines):
                stripped = line.strip()
                if "@" in stripped and any(method in stripped.upper() for method in [".GET(", ".POST(", ".PUT(", ".DELETE(", ".PATCH("]):
                    routes.append({"file": str(py_file.relative_to(project_path)), "line": str(i + 1), "route": stripped})
                if ".api_route(" in stripped or ".add_api_route(" in stripped:
                    routes.append({"file": str(py_file.relative_to(project_path)), "line": str(i + 1), "route": stripped})

    elif lang == "node":
        for ext in [".js", ".ts", ".jsx", ".tsx"]:
            for f in sorted(project_path.rglob(f"*{ext}")):
                content = _safe_read(f)
                for method in ["app.get(", "app.post(", "app.put(", "app.delete(", "app.patch(",
                               "router.get(", "router.post(", "router.put(", "router.delete("]:
                    for i, line in enumerate(content.split("\n")):
                        if method in line.strip().lower():
                            routes.append({"file": str(f.relative_to(project_path)), "line": str(i + 1), "route": line.strip()})

    elif lang == "java":
        for ext in [".java", ".kt"]:
            for f in sorted(project_path.rglob(f"*{ext}")):
                content = _safe_read(f)
                for i, line in enumerate(content.split("\n")):
                    stripped = line.strip()
                    for annot in ["@GetMapping", "@PostMapping", "@PutMapping", "@DeleteMapping", "@RequestMapping"]:
                        if annot in stripped:
                            routes.append({"file": str(f.relative_to(project_path)), "line": str(i + 1), "route": stripped})

    elif lang == "go":
        for f in sorted(project_path.rglob("*.go")):
            content = _safe_read(f)
            for i, line in enumerate(content.split("\n")):
                stripped = line.strip()
                for pattern in ["HandleFunc", "mux.Handle", "http.Handler", "gin.", "echo.", "fiber."]:
                    if pattern in stripped:
                        routes.append({"file": str(f.relative_to(project_path)), "line": str(i + 1), "route": stripped})

    return routes


def build_project_context(project_path: Path, lang: str = "") -> dict[str, Any]:
    """Build a complete context dictionary for a microservice project.

    This is the "web" the spider (LLM) sits in the middle of.
    It captures everything needed to understand and instrument a service.
    """
    if not lang:
        lang = _detect_language(project_path)

    main_file = _find_main_file(project_path, lang)
    deps_file = _find_deps_file(project_path, lang)

    framework = _detect_framework_from_files(project_path, lang)
    context: dict[str, Any] = {
        "language": lang,
        "service_name": _get_service_name(project_path),
        "framework": framework,
        "orm": _detect_orm(project_path, lang),
        "http_client": _detect_http_client(project_path, lang),
        "project_path": str(project_path),
        "main_file": str(main_file.relative_to(project_path)) if main_file else None,
        "deps_file": str(deps_file.relative_to(project_path)) if deps_file else None,
        "has_dockerfile": (project_path / "Dockerfile").exists(),
        "has_docker_compose": any(
            (d / "docker-compose.yml").exists()
            for d in [project_path, project_path.parent, project_path.parent.parent]
        ),
        "structure": _scan_project_structure(project_path),
        "api_routes": _scan_api_routes(project_path, lang),
        "main_content": _safe_read(main_file) if main_file else "",
        "deps_content": _safe_read(deps_file) if deps_file else "",
    }

    return context


_SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".mvn", "target", "build", "dist", ".gradle"}

def _scan_project_structure(project_path: Path) -> list[str]:
    """Scan the project tree and return a readable directory listing."""
    entries: list[str] = []
    root = project_path.resolve()

    for path in sorted(root.rglob("*")):
        if any(part.startswith(".") and part not in (".", "..") for part in path.relative_to(root).parts):
            continue
        if any(seg in _SKIP_DIRS for seg in path.parts):
            continue
        if path.is_file():
            try:
                relative = path.relative_to(root)
                entries.append(str(relative))
            except ValueError:
                pass

    return entries


def format_context_for_prompt(context: dict[str, Any]) -> str:
    """Format the project context dict into a readable text block for the LLM."""
    lines = [
        f"Language: {context.get('language', 'python')}",
        f"Service name: {context['service_name']}",
        f"Framework: {context['framework']}",
        f"ORM: {context['orm']}",
        f"HTTP client: {context['http_client']}",
        f"Main file: {context['main_file']}",
        f"Deps file: {context.get('deps_file')}",
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

    deps_content = context.get("deps_content", "")
    if deps_content:
        deps_file = context.get("deps_file", "deps")
        lines.append("")
        lines.append(f"--- {deps_file} ---")
        lines.append(deps_content)

    return "\n".join(lines)
