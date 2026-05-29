#!/usr/bin/env python3
"""
auto_instrument_and_analyze.py
==============================
One-shot script that:
  1. Instruments all microservices-school services with OpenTelemetry
  2. Adds Jaeger to the Docker Compose stack (via override)
  3. Builds & starts all services
  4. Generates traffic across all endpoints
  5. Collects traces from Jaeger for every service
  6. Runs the full SCOM analysis pipeline
  7. Optionally launches the dashboard

Usage:
    python auto_instrument_and_analyze.py [--project-path PATH] [--llm] [--yes]
"""

from __future__ import annotations

import argparse
import difflib
import os
import shutil
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_PROJECT_PATH = Path("../microservices-school/microservices-school").resolve()

SERVICES_WITH_DB = {"auth-service", "student-service", "classroom-service", "enrollment-service"}

JAEGER_COMPOSE_SERVICE = {
    "jaeger": {
        "image": "jaegertracing/all-in-one:latest",
        "container_name": "jaeger",
        "ports": [
            "16686:16686",
            "4317:4317",
        ],
        "networks": ["school_network"],
        "restart": "unless-stopped",
    }
}

OTEL_PACKAGES = [
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-grpc",
    "opentelemetry-instrumentation",
    "opentelemetry-instrumentation-fastapi",
    "opentelemetry-instrumentation-sqlalchemy",
    "opentelemetry-instrumentation-asyncpg",
]

OTEL_PACKAGES_NO_DB = [
    pkg for pkg in OTEL_PACKAGES
    if "sqlalchemy" not in pkg and "asyncpg" not in pkg
]

JAEGER_HOST_INTERNAL = "jaeger"
JAEGER_HOST_EXTERNAL = "localhost"
JAEGER_GRPC_PORT = 4317
JAEGER_UI_PORT = 16686

SERVICE_PORTS: dict[str, int] = {
    "auth-service": 8004,
    "student-service": 8001,
    "classroom-service": 8002,
    "enrollment-service": 8003,
    "gateway": 8000,
}

SERVICE_HEALTH_PATHS: dict[str, str] = {
    "auth-service": "/auth/health",
    "student-service": "/health",
    "classroom-service": "/health",
    "enrollment-service": "/health",
    "gateway": "/health",
}

GATEWAY_BASE = "http://localhost:8000"
GATEWAY_API = f"{GATEWAY_BASE}/api/v1"

# Wrapper template for services with async SQLAlchemy + asyncpg
WRAPPER_WITH_DB = '''\
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.trace import SpanKind
from opentelemetry.trace.status import Status, StatusCode
from sqlalchemy import event
from sqlalchemy.engine import Engine as SAEngine

_DB_SYSTEM = "db.system"
_DB_STATEMENT = "db.statement"
_SYSTEM_COMMANDS = frozenset({"BEGIN", "COMMIT", "ROLLBACK", "SET", "SHOW"})


@event.listens_for(SAEngine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    if getattr(context, "_otel_span", None) is not None:
        return
    if not statement or not statement.strip():
        return
    cmd = statement.strip().split()[0].upper()
    if cmd in _SYSTEM_COMMANDS:
        return
    span_name = f"{cmd} {statement.strip()[:60]}"
    attrs = {
        _DB_SYSTEM: "postgresql",
        _DB_STATEMENT: statement,
    }
    tracer = trace.get_tracer(__name__)
    span = tracer.start_span(span_name, kind=SpanKind.CLIENT, attributes=attrs)
    context._otel_span = span


@event.listens_for(SAEngine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    span = getattr(context, "_otel_span", None)
    if span is not None:
        span.end()


@event.listens_for(SAEngine, "handle_error")
def _handle_error(context):
    span = getattr(context.execution_context, "_otel_span", None)
    if span is not None and span.is_recording():
        span.set_status(Status(StatusCode.ERROR))
        span.end()


def init_tracing(app=None):
    resource = Resource.create({"service.name": "{{SERVICE_NAME}}"})
    exporter = OTLPSpanExporter(endpoint="http://{{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}")
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    if app is not None:
        FastAPIInstrumentor.instrument_app(app)
    else:
        FastAPIInstrumentor().instrument()
'''

# Wrapper template for services WITHOUT SQLAlchemy (gateway)
WRAPPER_NO_DB = '''\
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor


def init_tracing(app=None):
    resource = Resource.create({"service.name": "{{SERVICE_NAME}}"})
    exporter = OTLPSpanExporter(endpoint="http://{{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}")
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    if app is not None:
        FastAPIInstrumentor.instrument_app(app)
    else:
        FastAPIInstrumentor().instrument()
'''


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _info(msg: str) -> None:
    print(f"[INFO]  {msg}")


def _ok(msg: str) -> None:
    print(f"[OK]    {msg}")


def _warn(msg: str) -> None:
    print(f"[WARN]  {msg}")


def _error(msg: str) -> None:
    print(f"[ERROR] {msg}")


def _step(n: int, msg: str) -> None:
    print(f"\n{'-' * 60}")
    print(f"STEP {n}: {msg}")
    print(f"{'-' * 60}\n")


def _find_project_root() -> Path:
    """Return the absolute path to the measure-automation project root."""
    return Path(__file__).resolve().parent.parent


def _ensure_boundary_analyzer_importable() -> None:
    """Add src/ to sys.path if boundary_analyzer is not already importable."""
    try:
        import boundary_analyzer  # noqa: F401
    except ImportError:
        src_path = str(_find_project_root() / "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)


def _confirm(prompt: str, default: bool = False) -> bool:
    """Ask the user for y/N confirmation. Returns True if yes."""
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(prompt + suffix).strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return default
    if default:
        return answer != "n"
    return answer == "y"


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: SERVICE DISCOVERY
# ─────────────────────────────────────────────────────────────────────────────

def discover_services(project_path: Path) -> list[dict[str, Any]]:
    """Scan the project directory and discover all microservices.

    Returns a list of dicts with keys:
        name, path, port, has_db, framework, health_path, service_name
    """
    # Ensure we import from the installed package
    _ensure_boundary_analyzer_importable()
    from boundary_analyzer.auto_setup.setup_instrumentation import detect_framework

    services: list[dict[str, Any]] = []

    for entry in sorted(project_path.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue

        main_py = entry / "app" / "main.py"
        if not main_py.exists():
            continue

        name = entry.name
        has_db = name in SERVICES_WITH_DB
        framework = detect_framework(entry)
        port = SERVICE_PORTS.get(name, 8000)
        health_path = SERVICE_HEALTH_PATHS.get(name, "/health")

        services.append({
            "name": name,
            "path": entry,
            "port": port,
            "has_db": has_db,
            "framework": framework,
            "health_path": health_path,
            "service_name": name,
        })

    return services


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: JAEGER DOCKER COMPOSE OVERRIDE
# ─────────────────────────────────────────────────────────────────────────────

def ensure_jaeger_override(project_path: Path) -> Path:
    """Create or verify docker-compose.override.yml with Jaeger service.

    Returns the path to the override file.
    """
    override_path = project_path / "docker-compose.override.yml"

    if override_path.exists():
        try:
            with open(override_path, "r", encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
            services = existing.get("services", {})
            if "jaeger" in services:
                _info("Jaeger already present in docker-compose.override.yml — skipping.")
                return override_path
        except yaml.YAMLError:
            _warn("Existing docker-compose.override.yml is invalid YAML. Will overwrite after backup.")

        # Backup existing override
        backup = override_path.with_suffix(override_path.suffix + ".bak")
        shutil.copy2(override_path, backup)
        _info(f"Existing override backed up to: {backup}")

    with open(override_path, "w", encoding="utf-8") as f:
        yaml.dump({"services": JAEGER_COMPOSE_SERVICE}, f, default_flow_style=False, sort_keys=False)

    _ok(f"Jaeger added to docker-compose.override.yml")
    return override_path


def remove_jaeger_override(project_path: Path) -> None:
    """Remove the docker-compose.override.yml if it only contains Jaeger."""
    override_path = project_path / "docker-compose.override.yml"
    if not override_path.exists():
        return

    try:
        with open(override_path, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f) or {}
        services = content.get("services", {})
        # Only remove if the only service is Jaeger
        if list(services.keys()) == ["jaeger"]:
            override_path.unlink()
            _info("Removed docker-compose.override.yml (no longer needed).")
        else:
            _info("Keeping docker-compose.override.yml (contains additional services).")
    except yaml.YAMLError:
        _warn("Could not parse docker-compose.override.yml — leaving in place.")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: OTEL DEPENDENCIES
# ─────────────────────────────────────────────────────────────────────────────

def add_otel_to_requirements(service: dict[str, Any]) -> None:
    """Add required OTel packages to the service's requirements.txt.

    Also installs them on the host system.
    """
    req_path = service["path"] / "requirements.txt"
    if not req_path.exists():
        _warn(f"requirements.txt not found in {service['name']} — creating one.")
        req_path.write_text("", encoding="utf-8")

    current = req_path.read_text(encoding="utf-8")
    current_lines = [line.strip() for line in current.splitlines()]
    existing_packages = {line.split("==")[0].split(">=")[0].split("<=")[0].strip().lower()
                         for line in current_lines if line and not line.startswith("#") and " " not in line}

    packages_to_add = OTEL_PACKAGES_NO_DB if not service["has_db"] else OTEL_PACKAGES
    missing = [pkg for pkg in packages_to_add if pkg.lower() not in existing_packages]

    if not missing:
        _info(f"All OTel packages already present in {service['name']}/requirements.txt")
    else:
        with open(req_path, "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(missing) + "\n")
        _ok(f"Added to {service['name']}/requirements.txt: {', '.join(missing)}")

    # Install on host system too
    _ensure_boundary_analyzer_importable()
    from boundary_analyzer.auto_setup.setup_instrumentation import install_packages

    try:
        install_packages(service["framework"], service["path"])
    except SystemExit:
        _warn(f"pip install for {service['name']} had issues (may already be installed).")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: INSTRUMENTATION
# ─────────────────────────────────────────────────────────────────────────────

def _generate_wrapper_content(service: dict[str, Any]) -> str:
    """Generate the otel_instrumentation.py content for a service."""
    template = WRAPPER_WITH_DB if service["has_db"] else WRAPPER_NO_DB
    content = template.replace("{{SERVICE_NAME}}", service["service_name"])
    content = content.replace("{{JAEGER_HOST}}", JAEGER_HOST_INTERNAL)
    content = content.replace("{{JAEGER_GRPC_PORT}}", str(JAEGER_GRPC_PORT))
    return content


def _add_init_tracing_to_main(main_path: Path) -> str | None:
    """Add the init_tracing() call to the service's main.py.

    Returns the modified content, or None if already instrumented.
    """
    original = main_path.read_text(encoding="utf-8")

    if "init_tracing" in original:
        _info("main.py already contains init_tracing — skipping modification.")
        return None

    lines = original.splitlines()
    result: list[str] = []
    import_added = False
    init_added = False
    skip_until = -1

    IMPORT_ANCHORS = ["import structlog", "from app.core.config import get_settings"]

    for i, line in enumerate(lines):
        # Skip lines already consumed by the FastAPI block parser
        if i <= skip_until:
            continue

        # Add import after the last import anchor
        if not import_added:
            for anchor in IMPORT_ANCHORS:
                if anchor in line:
                    result.append(line)
                    if i + 1 < len(lines) and lines[i + 1].strip() == "":
                        result.append("")
                    result.append("from otel_instrumentation import init_tracing")
                    import_added = True
                    break
            if import_added:
                continue

        # Add init_tracing(app) AFTER the entire app = FastAPI(...) block
        if not init_added and "app = FastAPI(" in line and not line.strip().startswith("#"):
            # Emit the opening line
            result.append(line)
            # Track parenthesis depth to find the closing paren
            depth = line.count("(") - line.count(")")
            j = i + 1
            while j < len(lines) and depth > 0:
                next_line = lines[j]
                result.append(next_line)
                depth += next_line.count("(") - next_line.count(")")
                j += 1
            # Emit init_tracing(app) right after the closing paren line
            indent = ""
            stripped = line.lstrip()
            indent = line[:len(line) - len(stripped)]
            result.append(f"{indent}init_tracing(app)")
            init_added = True
            skip_until = j - 1
            continue

        result.append(line)

    if not import_added:
        result.insert(0, "from otel_instrumentation import init_tracing")
        result.insert(1, "")
        import_added = True

    if not init_added:
        for i, line in enumerate(result):
            if ".add_middleware(" in line and not init_added:
                indent = ""
                stripped = line.lstrip()
                indent = line[:len(line) - len(stripped)]
                result.insert(i, f"{indent}init_tracing(app)")
                init_added = True
                break

    if not init_added:
        _warn("Could not find a suitable location to insert init_tracing(app). "
              "Please add it manually after `app = FastAPI(...)`.")
        return None

    return "\n".join(result)


def instrument_service(
    service: dict[str, Any],
    use_llm: bool,
    auto_yes: bool,
    force: bool = False,
) -> bool:
    """Instrument a single service with OTel.

    Returns True on success, False on failure.
    """
    main_py = service["path"] / "app" / "main.py"
    if not main_py.exists():
        _error(f"main.py not found in {service['name']}")
        return False

    wrapper_path = service["path"] / "otel_instrumentation.py"
    wrapper_content = _generate_wrapper_content(service)

    # Check if already instrumented
    if "init_tracing" in main_py.read_text(encoding="utf-8"):
        if force:
            # Re-write wrapper file even if main.py already has init_tracing
            wrapper_path.write_text(wrapper_content, encoding="utf-8")
            _info(f"{service['name']} wrapper re-written (--force).")
            return True
        _info(f"{service['name']} already instrumented — skipping.")
        return True

    if use_llm:
        _ensure_boundary_analyzer_importable()
        from boundary_analyzer.llm.instrumentation import generate_instrumentation

        _info(f"Generating instrumentation for {service['name']} using LLM...")
        llm_code = generate_instrumentation(
            service["path"],
            jaeger_host=JAEGER_HOST_INTERNAL,
            jaeger_port=JAEGER_GRPC_PORT,
        )

        if llm_code is None:
            _warn(f"LLM generation failed for {service['name']}. Falling back to template.")
            use_llm = False
        else:
            original = main_py.read_text(encoding="utf-8")
            if original == llm_code:
                _ok("Code unchanged by LLM.")
                return True

            diff = difflib.unified_diff(
                original.splitlines(keepends=True),
                llm_code.splitlines(keepends=True),
                fromfile=str(main_py),
                tofile=str(main_py) + " (instrumented)",
            )
            print("\n" + "-" * 60)
            print(f"Proposed changes for {service['name']}:")
            print("-" * 60)
            for line in diff:
                print("  " + line.rstrip())
            print("-" * 60)

            if not auto_yes and not _confirm("Apply these changes?"):
                _info(f"Changes not applied for {service['name']}. Falling back to template.")
                use_llm = False
            else:
                backup = main_py.with_suffix(".py.bak")
                shutil.copy2(main_py, backup)
                main_py.write_text(llm_code, encoding="utf-8")
                _ok(f"Instrumentation written to {main_py}")
                _ok(f"Backup: {backup}")

                # Also write the wrapper file for reference
                wrapper_content = _generate_wrapper_content(service)
                wrapper_path = service["path"] / "otel_instrumentation.py"
                wrapper_path.write_text(wrapper_content, encoding="utf-8")
                return True

    if not use_llm:
        # Template mode
        _info(f"Instrumenting {service['name']} using template...")

        # Write wrapper file
        wrapper_content = _generate_wrapper_content(service)
        wrapper_path = service["path"] / "otel_instrumentation.py"
        wrapper_path.write_text(wrapper_content, encoding="utf-8")
        _ok(f"Wrapper written: {wrapper_path}")

        # Modify main.py
        modified = _add_init_tracing_to_main(main_py)
        if modified is None:
            return False

        if not auto_yes:
            original = main_py.read_text(encoding="utf-8")
            if modified != original:
                diff = difflib.unified_diff(
                    original.splitlines(keepends=True),
                    modified.splitlines(keepends=True),
                    fromfile=str(main_py),
                    tofile=str(main_py) + " (instrumented)",
                )
                print("\n" + "-" * 60)
                print(f"Proposed changes for {service['name']}:")
                print("-" * 60)
                for line in diff:
                    print("  " + line.rstrip())
                print("-" * 60)

                if not _confirm("Apply these changes?"):
                    _info(f"Changes not applied for {service['name']}.")
                    return False

        backup = main_py.with_suffix(".py.bak")
        shutil.copy2(main_py, backup)
        main_py.write_text(modified, encoding="utf-8")
        _ok(f"main.py modified for {service['name']}")
        _ok(f"Backup: {backup}")

    return True


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5: DOCKER COMPOSE
# ─────────────────────────────────────────────────────────────────────────────

def run_docker_compose(project_path: Path) -> bool:
    """Run docker compose up --build -d in the project directory.

    Returns True if successful.
    """
    compose_cmd = _find_docker_compose()
    if compose_cmd is None:
        _error("Docker Compose not found. Is Docker Desktop running?")
        return False

    cmd = compose_cmd.split() + ["up", "--build", "-d"]
    _info(f"Running: {' '.join(cmd)} in {project_path}")
    _info("This may take several minutes on first build...")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_path),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=600,
        )
        if result.returncode != 0:
            _error(f"Docker Compose failed with exit code {result.returncode}")
            stdout_tail = (result.stdout or "")[-1500:]
            stderr_tail = (result.stderr or "")[-1500:]
            _warn(f"stdout: {stdout_tail}")
            _warn(f"stderr: {stderr_tail}")
            return False
        _ok("All services started via Docker Compose.")
        return True
    except subprocess.TimeoutExpired:
        _error("Docker Compose timed out after 600 seconds.")
        return True  # Return True as containers may still be starting
    except FileNotFoundError:
        _error("Docker not found. Is Docker Desktop installed and running?")
        return False


def _find_docker_compose() -> str | None:
    """Find the docker compose command (new plugin or legacy binary)."""
    for cmd in ["docker compose", "docker-compose"]:
        parts = cmd.split()
        try:
            subprocess.run(
                parts + ["version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6: HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────────

def wait_for_services(services: list[dict[str, Any]], timeout: int = 120) -> bool:
    """Poll each service's health endpoint until all are healthy or timeout.

    Returns True if all services are healthy.
    """
    endpoints = []
    for svc in services:
        host = GATEWAY_BASE if svc["name"] == "gateway" else f"http://localhost:{svc['port']}"
        url = f"{host}{svc['health_path']}"
        endpoints.append((svc["name"], url))

    _info("Waiting for all services to be healthy...")
    start = time.time()
    healthy: set[str] = set()

    while time.time() - start < timeout:
        all_good = True
        for name, url in endpoints:
            if name in healthy:
                continue
            try:
                resp = requests.get(url, timeout=5)
                if resp.ok:
                    healthy.add(name)
                    _ok(f"{name} is healthy ({url})")
                else:
                    all_good = False
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.RequestException):
                all_good = False

        if all_good:
            _ok(f"All {len(services)} services healthy.")
            return True

        remaining = [s for s in services if s["name"] not in healthy]
        elapsed = int(time.time() - start)
        _info(f"Waiting for: {', '.join(s['name'] for s in remaining)} ({elapsed}s)")
        time.sleep(5)

    _error(f"Timeout after {timeout}s. Unhealthy services: "
           f"{', '.join(s['name'] for s in services if s['name'] not in healthy)}")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# STEP 7: TRAFFIC GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def generate_traffic() -> bool:
    """Generate HTTP traffic across all microservice endpoints.

    Returns True if at least some traffic was successfully generated.
    """
    _info("Generating traffic across all services...")

    _TO = 15
    ok: list[str] = []
    ko: list[str] = []
    token: str | None = None
    ts_ns = str(time.time_ns())
    ts_short = ts_ns[-6:]
    sid: str | None = None
    cid: str | None = None
    eid: str | None = None

    def _req(method: str, path: str, json_data: dict | None = None,
             auth: bool = False, label: str | None = None) -> requests.Response | None:
        nonlocal token
        url = f"{GATEWAY_API}{path}"
        headers = {"Content-Type": "application/json"}
        if auth and token:
            headers["Authorization"] = f"Bearer {token}"
        tag = label or f"{method} {url}"
        try:
            resp = requests.request(method, url, json=json_data,
                                    headers=headers, timeout=_TO)
            if resp.ok:
                ok.append(tag)
            else:
                ko.append(f"{tag} -> {resp.status_code}")
            return resp if resp.ok else None
        except requests.exceptions.RequestException as e:
            ko.append(f"{tag} -> {e}")
            return None

    # ── Auth (register may fail if already exists, login must work) ──────
    _req("POST", "/auth/register", {
        "username": "admin", "email": "admin@school.fr",
        "password": "Admin1234!", "role": "admin",
    })
    resp = _req("POST", "/auth/login", {
        "username": "admin", "password": "Admin1234!",
    })
    if resp:
        token = resp.json().get("access_token")
    if not token:
        _warn("No JWT — sending unauthenticated requests only.")

    # ── Student CRUD (unique email per run) ──────────────────────────────
    email_student = f"jean.{ts_short}@student.fr"
    resp = _req("POST", "/students", {
        "first_name": "Jean", "last_name": "Dupont",
        "email": email_student,
        "year_of_study": 2, "major": "Informatique",
    }, auth=True, label="POST /students (create)")
    if resp:
        sid = resp.json().get("id")

    _req("GET", "/students", auth=True, label="GET /students (list)")
    _req("GET", "/students/stats", auth=True, label="GET /students/stats")

    if sid:
        _req("GET", f"/students/{sid}", auth=True,
             label=f"GET /students/{{id}} ({sid[:8]}..)")
        _req("PUT", f"/students/{sid}", {
            "first_name": "Jean", "last_name": "Updated",
            "email": email_student, "year_of_study": 3, "major": "Informatique",
        }, auth=True, label=f"PUT /students/{{id}} ({sid[:8]}..)")

    # ── Classroom CRUD (try multiple room numbers in case of failures) ────
    for suffix in ["A101", "B202", "C303"]:
        if cid:
            break
        resp = _req("POST", "/classrooms", {
            "room_number": suffix, "name": f"Salle {suffix}",
            "building": suffix[0], "floor": 1, "capacity": 30,
            "room_type": "computer_lab",
            "has_projector": True, "has_computers": True,
        }, auth=True, label=f"POST /classrooms (try {suffix})")
        if resp:
            cid = resp.json().get("id")

    _req("GET", "/classrooms", auth=True, label="GET /classrooms (list)")

    if cid:
        _req("GET", f"/classrooms/{cid}", auth=True,
             label=f"GET /classrooms/{{id}} ({cid[:8]}..)")
        _req("PUT", f"/classrooms/{cid}", {"capacity": 35},
             auth=True, label=f"PUT /classrooms/{{id}} ({cid[:8]}..)")
        # Classroom schedule sub-resource
        resp = _req("POST", f"/classrooms/{cid}/schedules", {
            "course_name": "Maths", "teacher_name": "M. Robert",
            "day_of_week": "monday", "start_time": "08:00",
            "end_time": "10:00", "semester": "S1", "academic_year": "2025",
        }, auth=True, label=f"POST /classrooms/{{id}}/schedules")
        if resp:
            sched_id = resp.json().get("id")
            if sched_id:
                _req("DELETE", f"/classrooms/{cid}/schedules/{sched_id}",
                     auth=True, label="DELETE /classrooms/{id}/schedules/{sid}")

    # ── Enrollment CRUD ───────────────────────────────────────────────────
    _req("GET", "/enrollments", auth=True, label="GET /enrollments (list)")

    if sid and cid:
        resp = _req("POST", "/enrollments", {
            "student_id": sid, "classroom_id": cid,
        }, auth=True, label="POST /enrollments (create)")
        if resp:
            eid = resp.json().get("id")

    if eid:
        _req("GET", f"/enrollments/{eid}", auth=True,
             label=f"GET /enrollments/{{id}} ({eid[:8]}..)")
        _req("PUT", f"/enrollments/{eid}", {"status": "confirmed"},
             auth=True, label=f"PUT /enrollments/{{id}} ({eid[:8]}..)")
        _req("DELETE", f"/enrollments/{eid}", auth=True,
             label=f"DELETE /enrollments/{{id}} ({eid[:8]}..)")

    # ── Health (no auth) ─────────────────────────────────────────────────
    try:
        for hp in ["/health", "/health/all"]:
            r = requests.get(f"{GATEWAY_BASE}{hp}", timeout=_TO)
            ok.append(f"GET {hp}") if r.ok else ko.append(f"GET {hp} -> {r.status_code}")
    except requests.exceptions.RequestException as e:
        ko.append(f"health probes -> {e}")

    # ── Cleanup (delete created resources) ───────────────────────────────
    if eid:
        _req("DELETE", f"/enrollments/{eid}", auth=True,
             label=f"DELETE /enrollments/{{id}} ({eid[:8]}..)")
    if sid:
        _req("DELETE", f"/students/{sid}", auth=True,
             label=f"DELETE /students/{{id}} ({sid[:8]}..)")

    _info(f"Traffic summary: {len(ok)} succeeded, {len(ko)} failed.")
    if ko:
        _warn("Failures:\n  " + "\n  ".join(ko))
    return len(ok) > 0


# ─────────────────────────────────────────────────────────────────────────────
# STEP 8: TRACE COLLECTION
# ─────────────────────────────────────────────────────────────────────────────

def collect_all_traces(
    services: list[dict[str, Any]],
    output_dir: Path,
    limit: int = 500,
) -> bool:
    """Collect traces from Jaeger for all services.

    Returns True if at least one trace was collected.
    """
    _ensure_boundary_analyzer_importable()
    from boundary_analyzer.auto_setup.setup_instrumentation import collect_traces

    traces_dir = output_dir / "raw" / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    total_traces = 0
    for svc in services:
        output_path = traces_dir / f"{svc['service_name']}_traces.json"
        try:
            success = collect_traces(
                svc["service_name"],
                JAEGER_HOST_EXTERNAL,
                output_path,
                limit=limit,
            )
            if success:
                total_traces += 1
        except SystemExit:
            _warn(f"Trace collection for {svc['name']} failed.")

    if total_traces == 0:
        _warn("No traces collected from any service. "
              "Make sure traffic was sent and Jaeger is reachable.")
        return False

    _ok(f"Collected traces for {total_traces}/{len(services)} services.")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# STEP 9: PIPELINE + DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

def run_analysis_pipeline(
    output_dir: Path,
    threshold: float = 0.5,
    use_llm: bool = False,
    exclude_services: list[str] | None = None,
    exclude_health_routes: bool = True,
    exclude_http_client_spans: bool = True,
    exclude_unknown_endpoint: bool = True,
    skip_no_db_services: bool = False,
) -> bool:
    """Run the SCOM analysis pipeline on the collected traces.

    Returns True on success.
    """
    _ensure_boundary_analyzer_importable()
    from boundary_analyzer.pipeline.run_pipeline import run_pipeline

    traces_dir = output_dir / "raw" / "traces"
    if not traces_dir.exists() or not list(traces_dir.glob("*.json")):
        _error(f"No trace files found in {traces_dir}")
        return False

    _info("Running SCOM analysis pipeline...")
    try:
        rc = run_pipeline(
            traces=traces_dir,
            output_dir=output_dir,
            scom_method="weighted",
            threshold_method="fixed",
            fixed_threshold=threshold,
            exclude_services=exclude_services,
            exclude_health_routes=exclude_health_routes,
            exclude_http_client_spans=exclude_http_client_spans,
            exclude_unknown_endpoint=exclude_unknown_endpoint,
            skip_no_db_services=skip_no_db_services,
        )
        if rc != 0:
            _error(f"Pipeline returned exit code {rc}.")
            return False
        _ok("SCOM analysis complete.")
    except Exception as e:
        _error(f"Pipeline failed: {e}")
        return False

    # Optionally append LLM analysis
    if use_llm:
        _append_llm_analysis(output_dir)

    return True


def _append_llm_analysis(data_dir: Path) -> None:
    """Append AI-powered narrative analysis to the report."""
    _ensure_boundary_analyzer_importable()
    from boundary_analyzer.llm.analysis import generate_narrative_analysis

    rank_path = data_dir / "processed" / "service_rank.csv"
    mapping_path = data_dir / "interim" / "endpoint_table_map.csv"

    if not rank_path.exists() or not mapping_path.exists():
        _warn("Cannot append LLM analysis: rank or mapping file not found.")
        return

    _info("Generating AI narrative analysis...")
    analysis = generate_narrative_analysis(
        rank_path=rank_path,
        mapping_path=mapping_path,
        data_dir=data_dir,
    )

    if analysis is None:
        _warn("AI analysis returned no result (local fallback may have failed).")
        return

    report_path = data_dir / "report.md"
    try:
        with open(report_path, "a", encoding="utf-8") as f:
            f.write(f"\n\n---\n\n## AI-Powered Analysis\n\n{analysis}\n")
        _ok("AI analysis appended to report.")
    except OSError as e:
        _warn(f"Could not write AI analysis: {e}")


def launch_dashboard(data_dir: Path, host: str = "127.0.0.1", port: int = 8050) -> int:
    """Launch the Dash dashboard."""
    _ensure_boundary_analyzer_importable()
    from boundary_analyzer.cli import _run_dashboard

    _info(f"Launching dashboard at http://{host}:{port}")
    _info(f"Data source: {data_dir.resolve()}")
    return _run_dashboard(data_dir=data_dir, host=host, port=port)


# ─────────────────────────────────────────────────────────────────────────────
# CLEANUP
# ─────────────────────────────────────────────────────────────────────────────

def cleanup(project_path: Path, restore_override: bool = True) -> None:
    """Restore the project to its original state."""
    if restore_override:
        remove_jaeger_override(project_path)

    _info("Cleanup complete.")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Auto-instrument, collect traces, and analyze microservices-school.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          python scripts/auto_instrument_and_analyze.py
          python scripts/auto_instrument_and_analyze.py --llm
          python scripts/auto_instrument_and_analyze.py --llm --yes
          python scripts/auto_instrument_and_analyze.py --service student-service
        """),
    )

    parser.add_argument(
        "--project-path", "-p",
        type=Path,
        default=DEFAULT_PROJECT_PATH,
        help=f"Path to the microservices-school project (default: {DEFAULT_PROJECT_PATH})",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Use AI (LLM) to generate instrumentation code (requires OPENROUTER_API_KEY).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Auto-confirm all instrumentation changes without interactive prompts.",
    )
    parser.add_argument(
        "--service",
        default="",
        help="Instrument a single service only (default: all services).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="SCOM threshold for suspicious services (default: 0.5).",
    )
    parser.add_argument(
        "--no-dash",
        action="store_true",
        help="Skip launching the dashboard after analysis.",
    )
    parser.add_argument(
        "--dash-host",
        default="127.0.0.1",
        help="Dashboard host bind (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--dash-port",
        type=int,
        default=8050,
        help="Dashboard port (default: 8050).",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Do NOT remove docker-compose.override.yml after completion.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Output data directory (default: data/auto_run_<timestamp>/).",
    )
    parser.add_argument(
        "--exclude-services",
        nargs="*",
        default=["gateway"],
        help="Service names to exclude from SCOM analysis (default: gateway).",
    )
    parser.add_argument(
        "--no-exclude-health",
        action="store_false",
        dest="exclude_health_routes",
        help="Do NOT filter out health/infrastructure endpoints (/health, /metrics, etc).",
    )
    parser.add_argument(
        "--no-exclude-http-client",
        action="store_false",
        dest="exclude_http_client_spans",
        help="Do NOT filter out HTTP client spans (http send/receive).",
    )
    parser.add_argument(
        "--no-exclude-unknown-endpoint",
        action="store_false",
        dest="exclude_unknown_endpoint",
        help="Do NOT filter out unknown_endpoint entries from SCOM computation.",
    )
    parser.add_argument(
        "--skip-no-db-services",
        action="store_true",
        default=False,
        help="Exclude services with no DB tables detected from SCOM ranking.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-instrumentation even if already instrumented.",
    )

    args = parser.parse_args(argv)

    # ── Resolve paths ────────────────────────────────────────────────────────
    project_path = args.project_path.resolve()
    if not project_path.exists():
        _error(f"Project path not found: {project_path}")
        return 1

    # Use a dated run directory for isolation
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.data_dir or (_find_project_root() / "data" / f"auto_run_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'=' * 60}")
    print(f"  Auto Instrument & Analyze - Boundary Analyzer")
    print(f"  Project: {project_path}")
    print(f"  Output:  {output_dir}")
    print(f"{'=' * 60}\n")

    # ── STEP 1: Discover services ────────────────────────────────────────────
    _step(1, "Discovering services")
    all_services = discover_services(project_path)

    if not all_services:
        _error(f"No services found in {project_path}. "
               f"Expected subdirectories with app/main.py.")
        return 1

    if args.service:
        services = [s for s in all_services if s["name"] == args.service]
        if not services:
            _error(f"Service '{args.service}' not found. Available: "
                   f"{', '.join(s['name'] for s in all_services)}")
            return 1
    else:
        services = all_services

    for s in services:
        db_label = "DB" if s["has_db"] else "no DB"
        _info(f"  {s['name']} ({s['framework']}, {db_label}, port {s['port']})")
    _ok(f"Found {len(services)} service(s).")

    # ── STEP 2: Jaeger Docker Compose override ──────────────────────────────
    _step(2, "Adding Jaeger to Docker Compose")
    ensure_jaeger_override(project_path)

    # ── STEP 3: Add OTel dependencies ────────────────────────────────────────
    _step(3, "Adding OTel dependencies")
    for svc in services:
        add_otel_to_requirements(svc)

    # ── STEP 4: Instrument each service ──────────────────────────────────────
    _step(4, "Instrumenting services with OpenTelemetry")
    has_key = bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    if args.llm and not has_key:
        _warn("--llm flag set but OPENROUTER_API_KEY is not set. "
              "Falling back to template mode.")
        args.llm = False

    for svc in services:
        _info(f"Instrumenting {svc['name']}...")
        if not instrument_service(svc, use_llm=args.llm, auto_yes=args.yes, force=args.force):
            _warn(f"Instrumentation for {svc['name']} had issues. "
                  "You may need to manually verify.")

    # ── STEP 5: Start services via Docker Compose ───────────────────────────
    _step(5, "Starting services via Docker Compose")
    if not run_docker_compose(project_path):
        _error("Docker Compose failed. Check the Docker logs and try again.")
        return 1

    # Small pause for services to initialise
    time.sleep(5)

    # ── STEP 6: Wait for services to be healthy ─────────────────────────────
    _step(6, "Waiting for services to be healthy")
    all_healthy = wait_for_services(services)
    if not all_healthy:
        _warn("Some services are not healthy. Continuing with partial data.")

    # ── STEP 7: Generate traffic ────────────────────────────────────────────
    _step(7, "Generating traffic")
    traffic_ok = generate_traffic()

    # Wait for traces to propagate to Jaeger
    _info("Waiting 10 seconds for traces to propagate...")
    time.sleep(10)

    # ── STEP 8: Collect traces ──────────────────────────────────────────────
    _step(8, "Collecting traces from Jaeger")
    traces_ok = collect_all_traces(all_services, output_dir, limit=500)
    if not traces_ok:
        _warn("No traces collected. Analysis may produce empty results.")

    # ── STEP 9: Run analysis pipeline ───────────────────────────────────────
    _step(9, "Running SCOM analysis pipeline")
    pipeline_ok = run_analysis_pipeline(
        output_dir,
        threshold=args.threshold,
        use_llm=args.llm,
        exclude_services=args.exclude_services,
        exclude_health_routes=args.exclude_health_routes,
        exclude_http_client_spans=args.exclude_http_client_spans,
        exclude_unknown_endpoint=args.exclude_unknown_endpoint,
        skip_no_db_services=args.skip_no_db_services,
    )
    if not pipeline_ok and not args.no_dash:
        _warn("Pipeline had issues. Dashboard may show incomplete data.")

    # ── Cleanup ─────────────────────────────────────────────────────────────
    if not args.no_cleanup:
        cleanup(project_path, restore_override=True)

    # ── Launch dashboard ────────────────────────────────────────────────────
    if not args.no_dash:
        _step(10, "Launching dashboard")
        return launch_dashboard(
            data_dir=output_dir,
            host=args.dash_host,
            port=args.dash_port,
        )

    _ok("All done!")
    print(f"\n  Output dir: {output_dir.resolve()}")
    print(f"  Report:     {output_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
