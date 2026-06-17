from __future__ import annotations

import logging
from pathlib import Path

import yaml

from boundary_analyzer.auto.errors import AnalysisError, ErrorCode, unexpected
from boundary_analyzer.auto.models import ProjectInfo, ServiceInfo
from boundary_analyzer.auto.plugins import detect_language

logger = logging.getLogger(__name__)


def discover_project(root: str | Path) -> ProjectInfo:
    root_path = Path(root).resolve()

    if not root_path.exists():
        raise AnalysisError(
            code=ErrorCode.PROJECT_EMPTY,
            scope=str(root_path),
            _override_detail=f"Directory does not exist: {root_path}",
            recoverable=False,
        )

    if not any(root_path.iterdir()):
        raise AnalysisError(
            code=ErrorCode.PROJECT_EMPTY,
            scope=str(root_path),
            _override_detail="The project directory is empty.",
            recoverable=False,
        )

    try:
        plugin, detection = detect_language(root_path)
    except AnalysisError:
        raise
    except Exception as e:
        logger.exception("Language detection failed: %s", e)
        raise unexpected("discover", e)

    if detection.score < 0.3:
        raise AnalysisError(
            code=ErrorCode.LANG_UNSUPPORTED,
            scope=str(root_path),
            _override_detail=detection.detail,
            recoverable=False,
        )

    compose_services = _discover_compose_app_services(root_path)
    has_docker = bool(compose_services) or (root_path / "Dockerfile").exists()

    services: list[ServiceInfo] = []

    if compose_services:
        for compose_name, host_port, build_context in compose_services:
            if build_context and build_context.is_dir():
                try:
                    sub_plugin, sub_detection = detect_language(build_context)
                    entries = sub_detection.entries or sub_plugin.find_entry_points(build_context)
                    lang = sub_detection.language or detection.language
                    fw = sub_detection.framework or (entries[0].framework if entries else detection.framework)
                except (AnalysisError, Exception):
                    entries = []
                    lang = detection.language
                    fw = detection.framework
            else:
                entries = []
                lang = detection.language
                fw = detection.framework

            services.append(
                ServiceInfo(
                    name=compose_name,
                    language=lang,
                    framework=fw,
                    entry_points=entries[:1] if entries else [],
                    ports=[host_port] if host_port else [],
                    deployment="docker-compose",
                    compose_service_name=compose_name,
                )
            )
    else:
        # Try subdirectory scanning for monorepo layouts
        subdir_services = _discover_subdirectory_services(root_path)

        if subdir_services:
            for svc_name, host_port, build_context in subdir_services:
                if build_context and build_context.is_dir():
                    try:
                        sub_plugin, sub_detection = detect_language(build_context)
                        entries = sub_detection.entries or sub_plugin.find_entry_points(build_context)
                        lang = sub_detection.language or detection.language
                        fw = sub_detection.framework or (entries[0].framework if entries else detection.framework)
                    except (AnalysisError, Exception):
                        entries = []
                        lang = detection.language
                        fw = detection.framework
                else:
                    entries = []
                    lang = detection.language
                    fw = detection.framework

                services.append(
                    ServiceInfo(
                        name=svc_name,
                        language=lang,
                        framework=fw,
                        entry_points=entries[:1] if entries else [],
                        ports=[host_port] if host_port else [],
                        deployment="direct",
                    )
                )
        else:
            entries = detection.entries or plugin.find_entry_points(root_path)

            if not entries:
                raise AnalysisError(
                    code=ErrorCode.ENTRY_NOT_FOUND,
                    scope=str(root_path),
                    recoverable=False,
                )

            for entry in entries:
                port = plugin.guess_port(entry)
                service_name = _derive_service_name(entry.path, root_path)
                framework = detection.framework or plugin.detect_framework(root_path, entry)

                services.append(
                    ServiceInfo(
                        name=service_name,
                        language=detection.language,
                        framework=framework,
                        entry_points=[entry],
                        ports=[port] if port else [],
                        deployment="docker" if has_docker else "direct",
                    )
                )

    # Deduplicate services by (name, deployment)
    seen: set[tuple[str, str]] = set()
    unique_services: list[ServiceInfo] = []
    for svc in services:
        key = (svc.name, svc.deployment or "direct")
        if key not in seen:
            seen.add(key)
            unique_services.append(svc)
    services = unique_services

    return ProjectInfo(
        services=services,
        root_dir=root_path,
        has_docker=has_docker,
        language=detection.language,
        framework=detection.framework,
        plugins_loaded=[plugin.name],
    )


def _is_service_dir(path: Path) -> bool:
    """Heuristic: does this directory look like a microservice?"""
    indicators = [
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "pom.xml",
        "build.gradle",
        "composer.json",
        "go.mod",
        "Cargo.toml",
        "Dockerfile",
    ]
    return any((path / ind).exists() for ind in indicators)


def _discover_subdirectory_services(root: Path) -> list[tuple[str, int | None, Path | None]]:
    """Fallback: scan one level deep for service subdirectories."""
    results: list[tuple[str, int | None, Path | None]] = []
    seen: set[str] = set()

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name in {"node_modules", "__pycache__", "dist", "build", ".git", "venv", ".venv"}:
            continue
        if child.name in seen:
            continue
        if _is_service_dir(child):
            seen.add(child.name)
            results.append((child.name, None, child))

    return results


def _discover_compose_app_services(root: Path) -> list[tuple[str, int | None, Path | None]]:
    compose_file = None
    for name in ["docker-compose.yml", "docker-compose.yaml"]:
        p = root / name
        if p.exists():
            compose_file = p
            break

    if not compose_file:
        return []

    try:
        with open(compose_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, PermissionError, yaml.YAMLError) as e:
        logger.warning("Failed to parse compose file %s: %s", compose_file, e)
        return []

    if not data or "services" not in data:
        return []

    app_services: list[tuple[str, int | None, Path | None]] = []
    for svc_name, svc_config in data.get("services", {}).items():
        if "build" not in svc_config:
            continue

        host_port = None
        ports = svc_config.get("ports", [])
        for p in ports:
            if isinstance(p, str) and ":" in p:
                try:
                    host_port = int(p.rsplit(":", 1)[0].rsplit(":", 1)[0])
                except (ValueError, TypeError):
                    pass
                break
            elif isinstance(p, (int, str)):
                try:
                    host_port = int(p)
                except (ValueError, TypeError):
                    pass
                break

        build_context = None
        build_val = svc_config.get("build")
        if isinstance(build_val, str):
            build_context = (root / build_val).resolve()
        elif isinstance(build_val, dict):
            ctx = build_val.get("context", "")
            if ctx:
                build_context = (root / ctx).resolve()

        app_services.append((svc_name, host_port, build_context))

    return app_services


def _detect_docker(root: Path) -> bool:
    return (root / "docker-compose.yml").exists() or (root / "Dockerfile").exists()


def _derive_service_name(entry_path: Path, root: Path) -> str:
    try:
        rel = entry_path.relative_to(root)
    except ValueError:
        return entry_path.stem
    parts = rel.parts
    if len(parts) <= 1:
        return entry_path.stem
    return parts[-2]
