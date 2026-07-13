from __future__ import annotations

import collections
import json
import logging
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml

from boundary_analyzer.auto.errors import AnalysisError, ErrorCode
from boundary_analyzer.auto.models import ProjectInfo, ServiceInfo

logger = logging.getLogger(__name__)

_OTEL_FRAMEWORK_PACKAGES: dict[str, str] = {
    "flask": "opentelemetry-instrumentation-flask",
    "fastapi": "opentelemetry-instrumentation-fastapi",
    "django": "opentelemetry-instrumentation-django",
    "djangorest": "opentelemetry-instrumentation-django",
    "starlette": "opentelemetry-instrumentation-starlette",
    "tornado": "opentelemetry-instrumentation-tornado",
    "aiohttp": "opentelemetry-instrumentation-aiohttp-client",
}

_OTEL_DB_PACKAGES: list[str] = [
    "opentelemetry-instrumentation-psycopg2",
    "opentelemetry-instrumentation-sqlalchemy",
    "opentelemetry-instrumentation-dbapi",
    "opentelemetry-instrumentation-pymongo",
    "opentelemetry-instrumentation-redis",
    "opentelemetry-instrumentation-mysql",
    "opentelemetry-instrumentation-pymysql",
]


@dataclass
class DeployedService:
    service: ServiceInfo
    process: subprocess.Popen | None = None
    port: int | None = None
    pid: int | None = None
    ready: bool = False


@dataclass
class DeploymentResult:
    jaeger_port: int = 16686
    otlp_port: int = 4318
    services: list[DeployedService] = field(default_factory=list)

    @property
    def all_ready(self) -> bool:
        return all(s.ready for s in self.services) if self.services else False

    @property
    def any_ready(self) -> bool:
        return any(s.ready for s in self.services)

    @property
    def ready_services(self) -> list[DeployedService]:
        return [s for s in self.services if s.ready]


def _find_free_port(start: int = 8000, end: int = 9000) -> int:
    for port in range(start, end):
        if not _is_port_in_use(port):
            return port
    raise AnalysisError(
        code=ErrorCode.PORT_BIND_FAILED,
        scope=f"ports {start}-{end}",
        recoverable=False,
    )


def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def _wait_for_port(host: str, port: int, timeout: int = 30, interval: float = 0.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_port_in_use(port, host):
            return True
        time.sleep(interval)
    return False


def _wait_for_health(url: str, timeout: int = 30, interval: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code < 400:
                return True
        except (requests.RequestException, ConnectionError):
            pass
        time.sleep(interval)
    return False


def _wait_for_http_response(url: str, timeout: int = 60, interval: float = 2.0, label: str = "") -> bool:
    """Wait until *url* returns ANY HTTP response (any status code).

    Unlike :func:`_wait_for_health`, a 4xx/5xx counts as success: the goal is
    to know the application process is alive and answering, not that it is
    healthy — some apps have no route at "/" but are perfectly serving.

    When *label* is given, progress is printed every 30s: with budgets up to
    10 minutes, a silent wait is indistinguishable from a hang (a user
    watching a frozen terminal after the docker build output reported
    exactly that).
    """
    start = time.time()
    deadline = start + timeout
    next_progress = start + 30
    while time.time() < deadline:
        try:
            requests.get(url, timeout=5)
            if label and time.time() - start >= 30:
                sys.stderr.write(f"  ✔ {label} answered HTTP after {time.time() - start:.0f}s\n")
                sys.stderr.flush()
            return True
        except (requests.RequestException, ConnectionError):
            pass
        now = time.time()
        if label and now >= next_progress:
            sys.stderr.write(f"  … waiting for {label} to answer HTTP ({now - start:.0f}s / {timeout}s) — app still starting\n")
            sys.stderr.flush()
            next_progress = now + 30
        time.sleep(interval)
    return False


def _docker_installed() -> bool:
    try:
        result = subprocess.run(["docker", "--version"], capture_output=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _docker_daemon_ready() -> bool:
    """Return True if the Docker daemon is responding."""
    timeout = 25 if os.name == "nt" else 10
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=timeout,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def docker_available(retries: int = 3, delay: float = 3.0) -> bool:
    """Return True if Docker CLI is installed AND the daemon is responding.

    Retries up to ``retries`` times with ``delay`` seconds between attempts.
    """
    if not _docker_installed():
        return False
    for attempt in range(retries):
        if _docker_daemon_ready():
            return True
        if attempt < retries - 1:
            time.sleep(delay)
    return False


def _jaeger_alive(port: int) -> bool:
    """Return True if Jaeger is already running and healthy on this port."""
    if not _is_port_in_use(port):
        return False
    try:
        r = requests.get(f"http://127.0.0.1:{port}/api/services", timeout=5)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _parse_docker_error(captured_lines: list[str]) -> tuple[str, str | None, bool]:
    """Analyze captured Docker output for known failure patterns.

    Returns (detail, fix, recoverable) where:
    - detail: user-facing error description
    - fix: suggested fix command (or None)
    - recoverable: whether the error is potentially recoverable
    """
    full_output = "\n".join(captured_lines).lower()

    if "port is already allocated" in full_output or "port is already in use" in full_output:
        return (
            "One or more required ports are already in use by another container or process.",
            "Run: docker rm -f $(docker ps -aq --filter name=mba-jaeger) 2>nul, "
            "then: netstat -aon | findstr :4318 to find the process PID, "
            "then: taskkill /F /PID <PID>",
            True,
        )

    if "container name" in full_output and "is already in use" in full_output:
        return (
            "A container with the same name already exists from a previous run.",
            "Run: docker rm -f $(docker ps -aq --filter name=mba-jaeger) "
            "&& docker compose -f docker-compose.yml down --remove-orphans",
            True,
        )

    if "cannot connect to the docker daemon" in full_output:
        return (
            "Docker daemon stopped responding during deployment.",
            "Start Docker Desktop and wait for it to be ready, then re-run mba full.",
            True,
        )

    if "permission denied" in full_output:
        return (
            "Permission denied — Docker or file system access issue.",
            "Run Docker Desktop as administrator, or check file permissions on your project directory.",
            True,
        )

    if "no such image" in full_output:
        return (
            "A required Docker image could not be found or pulled.",
            "Check your internet connection and docker-compose.yml image references.",
            True,
        )

    if "network" in full_output and ("not found" in full_output or "already exists" in full_output):
        return (
            "Docker network error — previous networks may conflict.",
            "Run: docker network prune -f, then re-run mba full.",
            True,
        )

    if "pool overlaps" in full_output or "overlaps with other one" in full_output:
        return (
            "Docker network IP address pool overlaps with another network.",
            "Run: docker network prune -f, then re-run mba full.",
            True,
        )

    if "failed to solve" in full_output and ("did not find" in full_output or "no such host" in full_output):
        return (
            "Docker build failed because a dependency could not be fetched "
            "(pip registry failure or network issue).",
            "Check your internet connection and try again. If behind a proxy, verify HTTP_PROXY settings.",
            True,
        )

    if "failed to solve" in full_output:
        return (
            "Docker build failed — the image could not be built.",
            "Check the build output above for the specific error. "
            "Common issues: missing packages, pip install failures, or syntax errors in Dockerfile.",
            True,
        )

    if "error getting credentials" in full_output:
        return (
            "Docker registry credential error.",
            "Run: docker logout, then try again. If using Docker Desktop, check your login status.",
            True,
        )

    if "no matching manifest" in full_output or "not found in the manifest list" in full_output:
        return (
            "A Docker image does not support your platform architecture.",
            "Add the --platform flag (e.g., --platform=linux/amd64) to the service image in docker-compose.yml.",
            True,
        )

    if captured_lines:
        raw = "\n".join(captured_lines[-15:])
        return (
            f"Docker Compose failed. Last output:\n{raw}",
            "Inspect the error above, fix the issue, and re-run mba full.",
            True,
        )

    return (
        "Docker Compose failed to start services with no output.",
        "Run manually: docker compose up -d, then check the error.",
        True,
    )


def _resolve_external_jaeger_host(jaeger_port: int = 16686) -> tuple[str, str | None]:
    """Resolve the reachable hostname/IP for an externally-running Jaeger instance.

    Returns ``(otel_host, jaeger_container_name)`` where:
    - *otel_host* is the host or IP to use in the OTLP endpoint URL
    - *jaeger_container_name* is the name of a running Jaeger container
      (or *None* if no container was found)

    Priority:
    1. If Jaeger is in a Docker container, return its name + container name
       (we will connect it to the compose network later)
    2. Fall back to ``host.docker.internal`` w/ container_name = None
    """
    if not _docker_installed() or not _docker_daemon_ready():
        return "host.docker.internal", None

    # Try to find any Jaeger container currently running
    try:
        ps = subprocess.run(
            ["docker", "ps", "--filter", "publish=16686", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        container_names = [n.strip() for n in ps.stdout.splitlines() if n.strip()]
        if not container_names:
            ps = subprocess.run(
                ["docker", "ps", "--filter", "name=jaeger", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=10,
            )
            container_names = [n.strip() for n in ps.stdout.splitlines() if n.strip()]

        if container_names:
            jc = container_names[0]
            logger.info("External Jaeger container found: %s — will connect to compose network", jc)
            return jc, jc

    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: Docker gateway IP — works on Linux natively (not just Docker Desktop)
    try:
        gw = subprocess.run(
            ["docker", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}"],
            capture_output=True, text=True, timeout=10,
        )
        if gw.returncode == 0 and gw.stdout.strip():
            gw_ip = gw.stdout.strip()
            logger.info("Falling back to Docker gateway IP %s for external Jaeger", gw_ip)
            return gw_ip, None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Last resort: Docker Desktop only (macOS/Windows)
    return "host.docker.internal", None


def _resolve_compose_jaeger(
    jaeger_port: int = 16686,
    otlp_port: int = 4318,
    container_name: str = "mba-jaeger",
) -> tuple[bool, str, str | None]:
    """Decide whether to add Jaeger to the compose override or reuse a running instance.

    Returns ``(include_jaeger_service, otel_endpoint_host, jaeger_container_name)``.
    When an existing Jaeger is healthy, the third element is the Docker container name
    (or *None* if Jaeger is not containerised).
    """
    if _jaeger_alive(jaeger_port):
        logger.info("Reusing existing Jaeger on port %s", jaeger_port)
        sys.stderr.write(
            f"  ✔ Jaeger already running on port {jaeger_port} — reusing it\n"
        )
        sys.stderr.flush()
        otel_host, jc = _resolve_external_jaeger_host(jaeger_port)
        return False, otel_host, jc

    _ensure_jaeger_ports_free(jaeger_port, otlp_port, container_name)
    return True, container_name, None


def _ensure_jaeger_ports_free(
    jaeger_port: int = 16686,
    otlp_port: int = 4318,
    container_name: str = "mba-jaeger",
) -> None:
    """Check if Jaeger ports are free; try to clean up zombie containers.

    Raises AnalysisError with a clear fix message if a port remains in use.
    """
    if not _is_port_in_use(jaeger_port) and not _is_port_in_use(otlp_port):
        return

    logger.info("Port %s or %s is in use — attempting to remove leftover Jaeger container...", jaeger_port, otlp_port)
    sys.stderr.write(f"  ! Port {jaeger_port} or {otlp_port} in use — cleaning up old Jaeger container...\n")
    sys.stderr.flush()

    zombie_ids = set()
    try:
        ps1 = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={container_name}", "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=10,
        )
        for cid in ps1.stdout.splitlines():
            if cid.strip():
                zombie_ids.add(cid.strip())

        ps2 = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"publish={jaeger_port}", "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=10,
        )
        for cid in ps2.stdout.splitlines():
            if cid.strip():
                zombie_ids.add(cid.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if zombie_ids:
        sys.stderr.write(f"     Found {len(zombie_ids)} zombie container(s) — removing...\n")
        sys.stderr.flush()
        try:
            subprocess.run(
                ["docker", "rm", "-f"] + list(zombie_ids),
                capture_output=True, text=True, timeout=15,
            )
            time.sleep(1)
        except (FileNotFoundError, subprocess.SubprocessError):
            pass

    still_busy = [p for p in (jaeger_port, otlp_port) if _is_port_in_use(p)]
    if still_busy:
        ports_str = ", ".join(str(p) for p in still_busy)
        if _jaeger_alive(jaeger_port):
            logger.info(
                "Port(s) %s in use by a healthy Jaeger instance — caller should reuse it",
                ports_str,
            )
            return
        raise AnalysisError(
            code=ErrorCode.DOCKER_COMPOSE_FAILED,
            _override_detail=(
                f"Port(s) {ports_str} are still in use after removing zombie Jaeger containers. "
                "Another process or non-Jaeger container is holding the port.\n"
                f"Run: netstat -aon | findstr :{jaeger_port} to find the process PID, "
                f"then: taskkill /F /PID <PID>\n"
                "If you started Jaeger manually, ensure it responds at "
                f"http://127.0.0.1:{jaeger_port}/api/services or stop it first."
            ),
            recoverable=True,
        )

    logger.info("Removed leftover Jaeger container and freed ports")


def _docker_container_exists(name: str) -> bool:
    """Return True if a Docker container with the given name exists."""
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "exists", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def start_jaeger(
    jaeger_port: int = 16686,
    otlp_port: int = 4318,
    container_name: str = "mba-jaeger",
    timeout: int = 30,
) -> int:
    # Case 1 – already running and healthy
    if _jaeger_alive(jaeger_port):
        return jaeger_port

    if not _docker_installed():
        raise AnalysisError(
            code=ErrorCode.DOCKER_NOT_FOUND,
            recoverable=True,
        )

    if not docker_available():
        raise AnalysisError(
            code=ErrorCode.DOCKER_DAEMON_DOWN,
            recoverable=True,
        )

    _ensure_jaeger_ports_free(jaeger_port, otlp_port, container_name)

    # Case 2 – container exists but stopped → restart it
    if _docker_container_exists(container_name):
        try:
            subprocess.run(
                ["docker", "start", container_name],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise AnalysisError(
                code=ErrorCode.DOCKER_START_FAILED,
                _override_detail=e.stderr.strip() or str(e),
                recoverable=True,
            )
    else:
        # Case 3 – no container at all → create and run
        try:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    container_name,
                    "-p",
                    f"{jaeger_port}:16686",
                    "-p",
                    f"{otlp_port}:4318",
                    "-p",
                    "4317:4317",
                    "jaegertracing/all-in-one:latest",
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise AnalysisError(
                code=ErrorCode.DOCKER_PULL_FAILED,
                _override_detail=e.stderr.strip() or str(e),
                recoverable=True,
            )

    # Wait for Jaeger to become healthy
    if not _wait_for_port("127.0.0.1", jaeger_port, timeout=timeout):
        raise AnalysisError(
            code=ErrorCode.JAEGER_NOT_READY,
            _override_detail=f"Container started but port {jaeger_port} not listening after {timeout}s.",
            recoverable=True,
        )

    # Poll Jaeger API until it responds (replaces hardcoded time.sleep(3))
    _jaeger_deadline = time.time() + 10
    _jaeger_ready = False
    while time.time() < _jaeger_deadline:
        try:
            r = requests.get(f"http://127.0.0.1:{jaeger_port}/api/services", timeout=5)
            if r.status_code == 200:
                _jaeger_ready = True
                break
        except requests.RequestException:
            pass
        time.sleep(0.5)

    if not _jaeger_ready:
        raise AnalysisError(
            code=ErrorCode.JAEGER_NOT_READY,
            _override_detail="Jaeger API did not become ready within 10s after port check.",
            recoverable=True,
        )

    return jaeger_port


def stop_jaeger(container_name: str = "mba-jaeger") -> None:
    try:
        subprocess.run(
            ["docker", "stop", "--time", "5", container_name],
            capture_output=True,
            timeout=15,
        )
        subprocess.run(
            ["docker", "rm", container_name],
            capture_output=True,
            timeout=10,
        )
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        raise AnalysisError(
            code=ErrorCode.DOCKER_STOP_FAILED,
            _override_detail=f"Could not stop container {container_name}.",
        )


def _install_deps(service: ServiceInfo, plugin: Any) -> None:
    cmd = plugin.install_command(service.entry_points[0].path.parent)
    if cmd is None:
        return

    pip_packages = [
        "opentelemetry-sdk",
        "opentelemetry-exporter-otlp",
        "opentelemetry-instrumentation",
    ]

    install_cmd = [sys.executable, "-m", "pip", "install", "--quiet"] + pip_packages

    try:
        result = subprocess.run(
            install_cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip().lower()
            if "externally-managed-environment" in stderr:
                raise AnalysisError(
                    code=ErrorCode.PIP_PEP668,
                    _override_detail=result.stderr.strip(),
                    scope=service.name,
                    recoverable=True,
                )
            raise AnalysisError(
                code=ErrorCode.PIP_INSTALL_FAILED,
                _override_detail=result.stderr.strip(),
                scope=service.name,
                recoverable=True,
            )
    except FileNotFoundError:
        raise AnalysisError(
            code=ErrorCode.PIP_NOT_FOUND,
            scope=service.name,
            recoverable=True,
        )


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill a process and all its children (cross-platform)."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=5,
            )
        else:
            proc.kill()
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("Failed to kill process tree PID %s: %s", proc.pid, e)


def deploy_services(
    project: ProjectInfo,
    otlp_endpoint: str = "http://localhost:4318",
) -> DeploymentResult:
    from boundary_analyzer.auto.plugins import get_plugin_for_project

    plugin = get_plugin_for_project(project.root_dir)
    if plugin is None:
        raise AnalysisError(
            code=ErrorCode.DEPLOY_UNKNOWN,
            recoverable=False,
        )

    result = DeploymentResult()

    for service in project.services:
        if not service.entry_points:
            logger.warning("Skipping service %s — no entry points found", service.name)
            sys.stderr.write(f"  ! Skipping {service.name} (no entry points)\n")
            sys.stderr.flush()
            deployed = DeployedService(service=service, ready=False)
            result.services.append(deployed)
            continue
        entry = service.entry_points[0]

        try:
            _install_deps(service, plugin)
        except AnalysisError as e:
            if not e.recoverable:
                raise
            deployed = DeployedService(service=service, ready=False)
            result.services.append(deployed)
            continue

        port = service.port or plugin.guess_port(entry) or 8000
        if _is_port_in_use(port):
            port = _find_free_port(port + 1, port + 100)

        otel = plugin.instrument(entry, service.name, otlp_endpoint)

        env = os.environ.copy()
        env.update(otel.env_vars)

        run_cmd = plugin.run_command(entry, port)
        if run_cmd is None:
            deployed = DeployedService(service=service, ready=False)
            result.services.append(deployed)
            continue

        try:
            process = subprocess.Popen(
                run_cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=project.root_dir,
            )
        except FileNotFoundError:
            raise AnalysisError(
                code=ErrorCode.ENTRY_FAILED,
                scope=service.name,
                _override_detail=f"Command not found: {' '.join(run_cmd)}",
                recoverable=True,
            )

        ready = _wait_for_port("127.0.0.1", port, timeout=30)
        if not ready:
            _kill_process_tree(process)
            raise AnalysisError(
                code=ErrorCode.HEALTH_TIMEOUT,
                scope=f"{service.name} on :{port}",
                recoverable=True,
            )

        health_url = f"http://127.0.0.1:{port}{service.health_endpoint}"
        _wait_for_health(health_url, timeout=15)

        deployed = DeployedService(
            service=service,
            process=process,
            port=port,
            pid=process.pid,
            ready=True,
        )
        result.services.append(deployed)

    return result


_AGENT_DIR = Path.home() / ".mba" / "agents"
_AGENT_JAR_NAME = "opentelemetry-javaagent.jar"
_AGENT_JAR = _AGENT_DIR / _AGENT_JAR_NAME
_AGENT_URL = "https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/latest/download/opentelemetry-javaagent.jar"


def _ensure_java_agent() -> str | None:
    if _AGENT_JAR.exists():
        return str(_AGENT_DIR)

    try:
        _AGENT_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("  Downloading OTel Java agent from %s...", _AGENT_URL)
        resp = requests.get(_AGENT_URL, stream=True, timeout=120)
        resp.raise_for_status()
        with open(str(_AGENT_JAR), "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return str(_AGENT_DIR) if _AGENT_JAR.exists() else None
    except (requests.RequestException, OSError) as e:
        logger.warning("Failed to download agent JAR: %s", e)
        return None


#: Host-side cache where the OTel Node.js auto-instrumentation modules are
#: installed once, then bind-mounted read-only into Node containers — the
#: same self-contained pattern as the Java agent JAR. Injecting
#: ``NODE_OPTIONS=--require @opentelemetry/...`` *without* provisioning the
#: package only works if the application image happens to ship it; on any
#: real-world image it makes every node process (npm included) crash at
#: startup with MODULE_NOT_FOUND.
_NODE_OTEL_DIR = Path.home() / ".cache" / "boundary_analyzer" / "otel-node"

#: npm packages required for zero-code Node instrumentation. Pure-JS only
#: (no native builds), so one host-side install works for any container
#: platform/architecture.
_NODE_OTEL_PACKAGES = [
    "@opentelemetry/api@^1.9",
    "@opentelemetry/auto-instrumentations-node@^0.50",
]

#: In-container mount point for the provisioned modules. The require uses
#: the BARE package specifier (resolved through NODE_PATH) rather than an
#: absolute file path: modern @opentelemetry/auto-instrumentations-node
#: exposes ``./register`` only through its package.json export map (the
#: real file lives at build/src/register.js), and Node only consults export
#: maps for bare specifiers — an absolute ``.../register`` path 404s.
_NODE_OTEL_MOUNT = "/mba-otel-node"
_NODE_OTEL_REQUIRE = "@opentelemetry/auto-instrumentations-node/register"
_NODE_OTEL_NODE_PATH = f"{_NODE_OTEL_MOUNT}/node_modules"


def _ensure_node_otel() -> str | None:
    """Install the OTel Node auto-instrumentation into the host cache once.

    Returns the host directory to bind-mount (containing ``node_modules``),
    or ``None`` if npm is unavailable / the install failed — in which case
    the caller must NOT inject ``NODE_OPTIONS``, otherwise every node
    process in the container dies on a missing module.
    """
    # package.json is the stable presence marker — the register entrypoint's
    # on-disk location varies across package versions (export maps).
    marker = _NODE_OTEL_DIR / "node_modules" / "@opentelemetry" / "auto-instrumentations-node" / "package.json"
    if marker.exists():
        return str(_NODE_OTEL_DIR)

    npm = shutil.which("npm")
    if npm is None:
        logger.warning("npm not found on host — cannot provision Node OTel instrumentation; Node services will run untraced.")
        return None

    try:
        _NODE_OTEL_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("  Installing OTel Node auto-instrumentation into %s...", _NODE_OTEL_DIR)
        result = subprocess.run(
            [npm, "install", "--prefix", str(_NODE_OTEL_DIR), "--no-audit", "--no-fund", *_NODE_OTEL_PACKAGES],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            logger.warning("npm install of OTel Node packages failed: %s", (result.stderr or result.stdout).strip()[:500])
            return None
        return str(_NODE_OTEL_DIR) if marker.exists() else None
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("Failed to provision Node OTel instrumentation: %s", e)
        return None


def _find_compose_file(project_dir: Path) -> Path | None:
    # Delegate to the shared Compose-spec-ordered lookup so discovery and
    # deployment can never disagree about which file the project uses.
    from boundary_analyzer.auto.discover import find_compose_file

    return find_compose_file(project_dir)


def _parse_dockerfile_cmd(value: str) -> list[str] | None:
    """Parse a Dockerfile instruction value (exec JSON array or shell string)."""
    value = value.strip()
    if not value:
        return None

    # JSON exec form: CMD ["executable", "arg1"]
    if value.startswith("["):
        try:
            parts = json.loads(value)
            if isinstance(parts, list) and all(isinstance(p, str) for p in parts):
                return parts
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    # Shell form: CMD executable arg1
    try:
        return shlex.split(value, posix=True)
    except ValueError:
        return None


def _get_python_original_cmd(root_dir: Path, svc: ServiceInfo) -> list[str] | None:
    """Read the original CMD/ENTRYPOINT from the service's Dockerfile.

    Returns the command as a list suitable for wrapping with
    ``opentelemetry-instrument``, or *None* if the Dockerfile cannot be
    read or contains no runnable instruction.
    """
    compose_file = _find_compose_file(root_dir)
    if not compose_file:
        return None

    try:
        with open(compose_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, PermissionError, yaml.YAMLError):
        return None

    if not data or "services" not in data:
        return None

    svc_config = data.get("services", {}).get(svc.compose_service_name, {})
    if not svc_config:
        return None

    build_info = _get_build_info(root_dir, compose_file, svc.compose_service_name, svc_config)
    if build_info is None:
        return None

    df_path = build_info["df_path"]
    try:
        content = df_path.read_text(encoding="utf-8")
    except OSError:
        return None

    last_entrypoint: str | None = None
    last_cmd: str | None = None

    for line in content.splitlines():
        stripped = line.strip()
        up = stripped.upper()
        if up.startswith("CMD "):
            last_cmd = stripped[4:].strip()
        elif up.startswith("ENTRYPOINT "):
            last_entrypoint = stripped[11:].strip()

    cmd_parts = _parse_dockerfile_cmd(last_cmd) if last_cmd else None
    ep_parts = _parse_dockerfile_cmd(last_entrypoint) if last_entrypoint else None

    if ep_parts and cmd_parts:
        return ep_parts + cmd_parts
    if ep_parts:
        return ep_parts
    if cmd_parts:
        return cmd_parts

    return None


def _get_build_info(
    root_dir: Path,
    compose_file: Path,
    compose_service_name: str,
    svc_config: dict[str, Any],
) -> dict[str, Any] | None:
    """Extract build context and Dockerfile path from a compose service config."""
    build_val = svc_config.get("build")
    if isinstance(build_val, str):
        build_context = (root_dir / build_val).resolve()
        orig_build: dict[str, Any] = {"context": build_val}
    elif isinstance(build_val, dict):
        ctx = build_val.get("context", "")
        if ctx:
            build_context = (root_dir / ctx).resolve()
        else:
            return None
        orig_build = dict(build_val)
    else:
        return None

    df_name = orig_build.get("dockerfile", "Dockerfile")
    df_path = build_context / df_name
    if not df_path.exists():
        df_path = build_context / "dockerfile"
    if not df_path.exists():
        return None

    return {
        "build_context": build_context,
        "df_path": df_path,
        "orig_build": orig_build,
        "build_val": build_val,
    }


def _is_alpine_image(df_path: Path) -> bool:
    """Check if the Dockerfile uses an Alpine-based base image."""
    try:
        for line in df_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            up = stripped.upper()
            if up.startswith("FROM ") and ("ALPINE" in up or up.rstrip().endswith("-ALPINE")):
                return True
            if up.startswith("FROM "):
                return False
        return False
    except OSError:
        return False


def _generate_otel_dockerfile(root_dir: Path, svc: ServiceInfo) -> tuple[dict[str, Any] | None, list[str] | None]:
    """Generate a modified Dockerfile with OTel packages pre-installed.

    Returns (build_config, entrypoint) where:
    - build_config is the ``build`` section for the compose override (or *None*)
    - entrypoint is the entrypoint override (or *None*; the image's built-in
      ``ENTRYPOINT`` + ``CMD`` are used instead)
    """
    compose_file = _find_compose_file(root_dir)
    if not compose_file:
        logger.warning("No compose file found for %s", svc.compose_service_name)
        return None, None

    try:
        with open(compose_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, PermissionError, yaml.YAMLError) as e:
        logger.warning("Cannot read compose file for %s: %s", svc.compose_service_name, e)
        return None, None

    if not data or "services" not in data:
        logger.warning("No services in compose file for %s", svc.compose_service_name)
        return None, None

    svc_config = data.get("services", {}).get(svc.compose_service_name, {})
    if not svc_config:
        logger.warning("Service %s not found in compose file", svc.compose_service_name)
        return None, None

    build_info = _get_build_info(root_dir, compose_file, svc.compose_service_name, svc_config)
    if build_info is None:
        logger.warning("No build info found for %s", svc.compose_service_name)
        return None, None

    df_path = build_info["df_path"]
    try:
        content = df_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Cannot read Dockerfile for %s: %s", svc.compose_service_name, e)
        return None, None

    # Check there is at least one CMD or ENTRYPOINT to wrap
    has_runnable = any(
        line.strip().upper().startswith(("CMD ", "ENTRYPOINT "))
        for line in content.splitlines()
    )
    if not has_runnable:
        logger.warning("No CMD or ENTRYPOINT in Dockerfile for %s", svc.compose_service_name)
        return None, None

    lines = content.splitlines()
    last_run_idx = -1
    last_cmd_idx = -1
    last_ep_idx = -1

    for i, line in enumerate(lines):
        stripped = line.strip()
        up = stripped.upper()
        if up.startswith("CMD "):
            last_cmd_idx = i
        elif up.startswith("ENTRYPOINT "):
            last_ep_idx = i
        elif stripped.startswith("RUN "):
            last_run_idx = i

    insert_pos = max(last_run_idx + 1, 0)
    if last_cmd_idx >= 0 and insert_pos > last_cmd_idx:
        insert_pos = last_cmd_idx
    if last_ep_idx >= 0 and insert_pos > last_ep_idx:
        insert_pos = last_ep_idx

    fw_pkg = _OTEL_FRAMEWORK_PACKAGES.get(svc.framework, "")
    db_pkgs = " ".join(_OTEL_DB_PACKAGES)
    otel_pkgs = "opentelemetry-distro opentelemetry-api opentelemetry-sdk opentelemetry-instrumentation opentelemetry-exporter-otlp-proto-http"
    if fw_pkg:
        otel_pkgs += f" {fw_pkg}"
    if db_pkgs:
        otel_pkgs += f" {db_pkgs}"
    otel_run = f"RUN pip install --no-cache-dir {otel_pkgs}"

    num_inserted = 0
    # If base image is Alpine, install build deps before pip install
    if _is_alpine_image(df_path):
        apk_run = "RUN apk add --no-cache gcc musl-dev linux-headers"
        lines.insert(insert_pos, apk_run)
        insert_pos += 1
        num_inserted += 1

    lines.insert(insert_pos, otel_run)
    insert_pos += 1
    num_inserted += 1

    # Inject ENTRYPOINT directly into the Dockerfile so Docker uses it
    # at runtime, avoiding Docker Compose v5 clearing CMD when entrypoint
    # is overridden in the compose YAML.
    otel_entrypoint = 'ENTRYPOINT ["opentelemetry-instrument"]'
    if last_ep_idx >= 0:
        if (insert_pos - num_inserted) <= last_ep_idx:
            last_ep_idx += num_inserted
        lines[last_ep_idx] = otel_entrypoint
    else:
        if (insert_pos - num_inserted) <= last_cmd_idx:
            last_cmd_idx += num_inserted
        lines.insert(last_cmd_idx, otel_entrypoint)

    modified_content = "\n".join(lines)

    safe_name = svc.compose_service_name.replace("/", "_").replace("\\", "_")
    otel_df = build_info["build_context"] / f".mba-Dockerfile-{safe_name}"
    try:
        otel_df.write_text(modified_content, encoding="utf-8")
    except OSError as e:
        logger.warning("Cannot write .mba-Dockerfile for %s: %s", svc.compose_service_name, e)
        return None, None

    if isinstance(build_info["build_val"], str):
        build_config: dict[str, Any] = {
            "context": build_info["orig_build"]["context"],
            "dockerfile": f".mba-Dockerfile-{safe_name}",
        }
    else:
        build_config = dict(build_info["orig_build"])
        build_config["dockerfile"] = f".mba-Dockerfile-{safe_name}"

    return build_config, None


def find_otel_dockerfiles(project_root: Path) -> list[Path]:
    """Return paths of all .mba-Dockerfile files generated during this run."""
    results: list[Path] = []
    compose_file = _find_compose_file(project_root)
    if not compose_file:
        return results

    try:
        with open(compose_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, PermissionError, yaml.YAMLError):
        return results

    if not data or "services" not in data:
        return results

    for _svc_name, svc_config in data.get("services", {}).items():
        if "build" not in svc_config:
            continue
        build_val = svc_config.get("build")
        if isinstance(build_val, str):
            build_context = (project_root / build_val).resolve()
        elif isinstance(build_val, dict):
            ctx = build_val.get("context", "")
            if ctx:
                build_context = (project_root / ctx).resolve()
            else:
                continue
        else:
            continue

        for f in build_context.glob(".mba-Dockerfile*"):
            if f.is_file():
                results.append(f)

    return results


def _read_compose_networks(project_dir: Path) -> list[str]:
    """Return the top-level network names declared in the project's compose file."""
    compose_file = _find_compose_file(project_dir)
    if compose_file is None:
        return []
    try:
        data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    networks = data.get("networks") if isinstance(data, dict) else None
    if not isinstance(networks, dict):
        return []
    return sorted(networks.keys())


def _build_compose_override(
    project: ProjectInfo,
    jaeger_port: int = 16686,
    otlp_port: int = 4318,
    container_name: str = "mba-jaeger",
    include_jaeger: bool = True,
    otel_host: str | None = None,
) -> str:
    if not include_jaeger:
        otel_host = "host.docker.internal"
    else:
        otel_host = otel_host or container_name
    override: dict[str, Any] = {"services": {}}

    if include_jaeger:
        jaeger_svc: dict[str, Any] = {
            "image": "jaegertracing/all-in-one:latest",
            "ports": [
                f"{jaeger_port}:16686",
                f"{otlp_port}:4318",
            ],
        }
        # When the project's compose declares named networks, its services
        # live on those networks — NOT on "default". Without joining them,
        # the app containers cannot resolve the Jaeger hostname and every
        # span export dies with getaddrinfo ENOTFOUND (found live on
        # docker/awesome-compose react-express-mysql, which uses
        # public/private networks).
        project_networks = _read_compose_networks(project.root_dir)
        if project_networks:
            jaeger_svc["networks"] = project_networks
        override["services"][container_name] = jaeger_svc

    for svc in project.services:
        if not svc.compose_service_name:
            continue

        svc_config: dict[str, Any] = {}
        if include_jaeger:
            svc_config["depends_on"] = {
                container_name: {"condition": "service_started"},
            }
        else:
            svc_config["extra_hosts"] = ["host.docker.internal:host-gateway"]

        if svc.language == "python":
            env = [
                f"OTEL_SERVICE_NAME={svc.name}",
                f"OTEL_EXPORTER_OTLP_ENDPOINT=http://{otel_host}:4318",
                "OTEL_TRACES_EXPORTER=otlp_proto_http",
                "OTEL_METRICS_EXPORTER=none",
                "OTEL_LOGS_EXPORTER=none",
            ]
            build_config, _otel_entrypoint = _generate_otel_dockerfile(project.root_dir, svc)
            if build_config:
                svc_config["build"] = build_config
            else:
                logger.warning(
                    "Could not patch Dockerfile for %s — deploying without OTel packages. "
                    "OTel env vars will be set but the app won't be instrumented.",
                    svc.compose_service_name,
                )

        elif svc.language == "java":
            env = [
                f"OTEL_SERVICE_NAME={svc.name}",
                f"OTEL_EXPORTER_OTLP_ENDPOINT=http://{otel_host}:4317",
            ]
            agent_host = _ensure_java_agent()
            if agent_host:
                env.append(f"JAVA_TOOL_OPTIONS=-javaagent:/mba-agent/{_AGENT_JAR_NAME}")
                env.append("OTEL_METRICS_EXPORTER=none")
                env.append("OTEL_LOGS_EXPORTER=none")
                svc_config["volumes"] = [
                    f"{agent_host}:/mba-agent:ro",
                ]

        elif svc.language == "node":
            # The JS SDK's register entrypoint defaults to the http/protobuf
            # OTLP protocol, which speaks to port 4318 — pointing it at the
            # gRPC port (4317) silently exports nothing.
            #
            # ENABLED_INSTRUMENTATIONS + RESOURCE_DETECTORS are restricted
            # because NODE_OPTIONS applies to EVERY node process in the
            # container (npm, nodemon, each Docker HEALTHCHECK invocation…),
            # and the full auto-instrumentation set plus the cloud resource
            # detectors (GCP/AWS metadata probes that wait out network
            # timeouts — observed live as GET /computeMetadata/v1/instance
            # spans) multiply into minutes of extra startup per boot. That
            # pushed a real app's readiness past the 300s budget.
            env = [
                f"OTEL_SERVICE_NAME={svc.name}",
                f"OTEL_EXPORTER_OTLP_ENDPOINT=http://{otel_host}:4318",
                "OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf",
                "OTEL_METRICS_EXPORTER=none",
                "OTEL_LOGS_EXPORTER=none",
                "OTEL_NODE_ENABLED_INSTRUMENTATIONS="
                "http,express,koa,fastify,router,mysql,mysql2,pg,mongodb,mongoose,redis,ioredis,knex,dns,net",
                "OTEL_NODE_RESOURCE_DETECTORS=env,host,os,serviceinstance",
            ]
            node_otel_host = _ensure_node_otel()
            if node_otel_host:
                # Same self-contained pattern as the Java agent: modules live
                # in a host cache, bind-mounted read-only. NODE_PATH makes the
                # bare specifier resolvable so its package export map applies.
                env.append(f"NODE_OPTIONS=--require {_NODE_OTEL_REQUIRE}")
                env.append(f"NODE_PATH={_NODE_OTEL_NODE_PATH}")
                svc_config["volumes"] = [
                    f"{node_otel_host}:{_NODE_OTEL_MOUNT}:ro",
                ]
            else:
                logger.warning(
                    "Node OTel modules unavailable — service '%s' will run WITHOUT tracing "
                    "(deploy proceeds, but no spans will be collected from it).",
                    svc.name,
                )

        elif svc.language == "php":
            env = [
                f"OTEL_SERVICE_NAME={svc.name}",
                f"OTEL_EXPORTER_OTLP_ENDPOINT=http://{otel_host}:4318",
                "OTEL_PHP_AUTOLOAD_ENABLED=true",
                "OTEL_METRICS_EXPORTER=none",
                "OTEL_LOGS_EXPORTER=none",
            ]

        elif svc.language == "dotnet":
            env = [
                f"OTEL_SERVICE_NAME={svc.name}",
                f"OTEL_EXPORTER_OTLP_ENDPOINT=http://{otel_host}:4317",
                "OTEL_DOTNET_AUTO_TRACES_EXPORTER=otlp",
                "OTEL_DOTNET_AUTO_METRICS_EXPORTER=none",
                "OTEL_DOTNET_AUTO_LOGS_EXPORTER=none",
                "OTEL_DOTNET_AUTO_FLUSH_ON_UNHANDLEDEXCEPTION=true",
            ]

        else:
            env = [
                f"OTEL_SERVICE_NAME={svc.name}",
                f"OTEL_EXPORTER_OTLP_ENDPOINT=http://{otel_host}:4318",
            ]

        svc_config["environment"] = env
        override["services"][svc.compose_service_name] = svc_config

    return yaml.dump(override, default_flow_style=False, sort_keys=False)


def _get_container_id_for_compose_service(
    compose_file: Path, override_file: Path | None, service_name: str,
) -> str | None:
    """Resolve the Docker container name/ID for a Docker Compose service.

    ``docker inspect <compose_service_name>`` does **not** work because Compose
    creates containers named ``<project>_<service>_<index>`` (e.g. ``scenario1_setup_1``).
    This helper uses ``docker compose ps -q`` to get the real container ID.
    """
    cmd = ["docker", "compose", "-f", str(compose_file)]
    if override_file and override_file.exists():
        cmd.extend(["-f", str(override_file)])
    cmd.extend(["ps", "-q", service_name])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        cid = result.stdout.strip()
        return cid if cid else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _get_container_networks(container_identifier: str) -> list[str]:
    """Return the list of Docker networks a container is connected to."""
    try:
        result = subprocess.run(
            [
            "docker", "inspect", container_identifier,
            "--format", "{{range $netName, $netConfig := .NetworkSettings.Networks}}{{$netName}}{{\"\\n\"}}{{end}}",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
        return [n.strip() for n in result.stdout.splitlines() if n.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def _get_compose_project_name(compose_file: Path) -> str:
    """Infer the Docker Compose project name from the compose file path.

    Compose v2 derives the default project name from the directory name,
    lowercased, keeping ``[a-z0-9_-]`` — hyphens are VALID and preserved
    (``react-express-mysql`` stays ``react-express-mysql``). The previous
    implementation replaced every non-alphanumeric character with ``_``,
    so the network-label lookup polled a project that never existed
    ("react_express_mysql", 47 fruitless polls observed live).
    """
    name = compose_file.parent.name.lower()
    safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in name)
    safe = safe.lstrip("_-")  # must start with a letter or digit
    return safe or "default"


def _connect_with_network_name(
    jaeger_container: str, network_name: str,
) -> bool:
    """Connect *jaeger_container* to *network_name*, handling 'already connected'."""
    try:
        jaeger_nets = _get_container_networks(jaeger_container)
        if network_name in jaeger_nets:
            logger.info("Jaeger already connected to network %s", network_name)
            return True

        sys.stderr.write(f"  ● Connecting {jaeger_container} to {network_name}...\n")
        sys.stderr.flush()
        connect = subprocess.run(
            ["docker", "network", "connect", network_name, jaeger_container],
            capture_output=True, text=True, timeout=15,
        )
        if connect.returncode == 0:
            sys.stderr.write(f"  ✔ {jaeger_container} connected to {network_name}\n")
            sys.stderr.flush()
            logger.info("Connected Jaeger container %s to network %s", jaeger_container, network_name)
            return True

        stderr = connect.stderr.strip()
        if "already exists" in stderr.lower():
            logger.info("Jaeger already connected to network %s", network_name)
            return True

        logger.warning("Failed to connect Jaeger to %s: %s", network_name, stderr)
        return False
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("Network connect failed: %s", e)
        return False


def _poll_compose_network_and_connect_jaeger(
    compose_file: Path,
    jaeger_container: str,
    poll_timeout: int = 60,
) -> bool:
    """Wait for the Docker Compose network to be created, then connect Jaeger.

    This is called **immediately** after ``docker compose up`` starts, before
    services finish building, so that by the time application code runs Jaeger
    is already reachable via Docker DNS (``<container_name>``).

    Returns True if the connection was made.
    """
    project_name = _get_compose_project_name(compose_file)
    deadline = time.time() + poll_timeout
    polled = 0

    while time.time() < deadline:
        try:
            result = subprocess.run(
                [
                    "docker", "network", "ls",
                    "--filter", f"label=com.docker.compose.project={project_name}",
                    "--format", "{{.Name}}",
                ],
                capture_output=True, text=True, timeout=10,
            )
            networks = [n.strip() for n in result.stdout.splitlines() if n.strip()]
            if networks:
                all_ok = True
                for net in networks:
                    if not _connect_with_network_name(jaeger_container, net):
                        all_ok = False
                return all_ok
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        time.sleep(1)
        polled += 1

    logger.warning(
        "Compose network for project '%s' not found after %d polls — "
        "trying fallback via service inspection",
        project_name, polled,
    )
    return False


def _connect_jaeger_to_compose_network(
    compose_file: Path,
    override_file: Path | None,
    jaeger_container: str,
    project: ProjectInfo,
) -> bool:
    """Connect the external Jaeger container to the Docker Compose project network.

    This is the **fallback** path used after containers are up.  Prefer
    ``_poll_compose_network_and_connect_jaeger`` (called right after compose
    starts) so that Jaeger is already on the network when application code runs.

    Returns True if the connection was made (or Jaeger was already on the network).
    """
    if not _docker_installed() or not _docker_daemon_ready():
        return False

    # Collect ALL unique compose networks across all service containers
    all_nets: set[str] = set()
    for svc in project.services:
        if not svc.compose_service_name:
            continue
        cid = _get_container_id_for_compose_service(compose_file, override_file, svc.compose_service_name)
        if not cid:
            continue
        nets = _get_container_networks(cid)
        all_nets.update(nets)

    if not all_nets:
        logger.warning("Could not determine any compose network — cannot connect Jaeger")
        return False

    all_ok = True
    for net in sorted(all_nets):
        if not _connect_with_network_name(jaeger_container, net):
            all_ok = False
    return all_ok


def _check_container_alive(
    compose_service_name: str,
    compose_file: Path | None = None,
    override_file: Path | None = None,
) -> tuple[bool, str]:
    """Check if a Docker Compose container is still running.

    Uses ``docker compose ps -q`` to resolve the real container name/ID
    (Docker Compose names containers ``<project>_<service>_<index>``).

    Args:
        compose_service_name: Service name in docker-compose.yml (e.g. ``setup``).
        compose_file: Path to the compose file.  If *None*, tries to auto-detect
            from ``CWD``.
        override_file: Optional compose override file.

    Returns (alive, log_snippet) where log_snippet is the tail of container
    logs (up to 20 lines) if the container has exited.
    """
    if not _docker_installed() or not _docker_daemon_ready():
        return True, ""  # can't check, assume alive

    if compose_file is None:
        from pathlib import Path
        compose_file = _find_compose_file(Path.cwd()) or Path()

    # Resolve the actual container ID via compose ps -q
    cid = _get_container_id_for_compose_service(
        compose_file, override_file, compose_service_name,
    )
    if not cid:
        logger.warning("Could not resolve container ID for %s — assuming not alive", compose_service_name)
        return False, ""

    try:
        inspect = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", cid],
            capture_output=True, text=True, timeout=10,
        )
        if inspect.returncode != 0:
            return True, ""
        status = inspect.stdout.strip()
        if status == "running":
            return True, ""
        if status in ("exited", "dead", "paused"):
            logs = subprocess.run(
                ["docker", "logs", "--tail", "20", cid],
                capture_output=True, text=True, timeout=10,
            )
            snippet = logs.stdout.strip() or logs.stderr.strip() or "(no logs)"
            return False, snippet
        return True, ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return True, ""


def deploy_docker_compose(
    project: ProjectInfo,
    jaeger_port: int = 16686,
    otlp_port: int = 4318,
    container_name: str = "mba-jaeger",
    # 600s: real cold starts run long — measured precisely on Docker's
    # react-express-mysql sample: MariaDB was ready in 3s, but the app
    # (npm → nodemon → node with bind-mounted volumes on Windows/WSL2 I/O)
    # took 5m40s from container start to "Webserver is ready", overshooting
    # the previous 300s budget. Fast apps are unaffected: the readiness
    # loop returns on the first HTTP response.
    timeout: int = 600,
) -> DeploymentResult:
    compose_file = _find_compose_file(project.root_dir)
    if compose_file is None:
        raise AnalysisError(
            code=ErrorCode.DEPLOY_UNKNOWN,
            scope=str(project.root_dir),
            _override_detail="No compose file found (looked for compose.yaml, compose.yml, docker-compose.yml, docker-compose.yaml).",
            recoverable=False,
        )

    if not _docker_installed():
        raise AnalysisError(
            code=ErrorCode.DOCKER_NOT_FOUND,
            recoverable=True,
        )

    if not docker_available():
        raise AnalysisError(
            code=ErrorCode.DOCKER_DAEMON_DOWN,
            recoverable=True,
        )

    include_jaeger, otel_host, jaeger_container_name = _resolve_compose_jaeger(
        jaeger_port, otlp_port, container_name,
    )

    result = DeploymentResult(
        jaeger_port=jaeger_port,
        otlp_port=otlp_port,
    )

    override_yaml = _build_compose_override(
        project,
        jaeger_port,
        otlp_port,
        container_name,
        include_jaeger=include_jaeger,
        otel_host=otel_host,
    )
    override_file = project.root_dir / ".mba-compose-override.yml"
    try:
        override_file.write_text(override_yaml, encoding="utf-8")
    except OSError as e:
        raise AnalysisError(
            code=ErrorCode.FILE_CLEANUP_FAILED,
            scope=str(override_file),
            _override_detail=f"Cannot write override file: {e}",
            recoverable=True,
        )

    cmd = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "-f",
        str(override_file),
        "up",
        "-d",
        "--build",
        "--remove-orphans",
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        _remove_override_file(override_file)
        raise AnalysisError(
            code=ErrorCode.DOCKER_NOT_FOUND,
            recoverable=True,
        )

    captured_lines: collections.deque[str] = collections.deque(maxlen=60)

    def _reader(p: subprocess.Popen, buf: collections.deque) -> None:
        if p.stdout is None:
            raise AnalysisError(code=ErrorCode.SUBPROCESS_FAILED, _override_detail="No stdout from compose build")
        try:
            for line in p.stdout:
                sys.stderr.write(line)
                sys.stderr.flush()
                buf.append(line.rstrip("\r\n"))
        except ValueError:
            pass

    reader = threading.Thread(target=_reader, args=(proc, captured_lines), daemon=True)
    reader.start()

    # If using external Jaeger, connect it to the compose network as soon as
    # the network exists — BEFORE services finish building/starting — so that
    # application code can resolve the Jaeger container via Docker DNS.
    if jaeger_container_name and include_jaeger is False:
        _poll_compose_network_and_connect_jaeger(
            compose_file, jaeger_container_name,
        )
    # (When include_jaeger is True, Jaeger is part of the compose override and
    #  is automatically on the compose network — no action needed.)

    # 900s, not 300: a first cold `docker compose up --build` on a real
    # project legitimately exceeds 5 minutes (measured 391s+ on Docker's own
    # react-express-mysql sample — `npm ci` for the React frontend alone
    # takes ~2 min). 300s killed builds that were seconds from finishing;
    # subsequent builds hit the layer cache and finish fast either way.
    try:
        retcode = proc.wait(timeout=900)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        if proc.stdout:
            proc.stdout.close()
        reader.join(timeout=5)
        _remove_override_file(override_file)
        detail_lines = list(captured_lines)
        detail = "\n".join(detail_lines[-20:]) if detail_lines else "docker compose timed out after 900 seconds."
        logger.warning("Docker Compose build timed out — see output above")
        raise AnalysisError(
            code=ErrorCode.DOCKER_COMPOSE_FAILED,
            _override_detail=detail,
            recoverable=True,
        )

    if proc.stdout:
        proc.stdout.close()
    reader.join(timeout=5)

    if retcode != 0:
        _remove_override_file(override_file)
        detail_lines = list(captured_lines)
        detail, fix, recoverable = _parse_docker_error(detail_lines)
        raise AnalysisError(
            code=ErrorCode.DOCKER_COMPOSE_FAILED,
            _override_detail=f"{detail}\n\nFix: {fix}",
            recoverable=recoverable,
        )

    for svc in project.services:
        if not svc.ports:
            deployed = DeployedService(service=svc, ready=False)
            result.services.append(deployed)
            continue

        port = svc.port
        if port is None:
            deployed = DeployedService(service=svc, ready=False)
            result.services.append(deployed)
            continue

        # Check if the container is still running (it may have exited/crashed)
        container_ok, container_logs = _check_container_alive(
            svc.compose_service_name,
            compose_file=compose_file,
            override_file=override_file,
        )
        if not container_ok and container_logs:
            logger.warning(
                "Container %s exited before port check — logs:\n%s",
                svc.compose_service_name, container_logs,
            )
            sys.stderr.write(
                f"  ✘ Container {svc.compose_service_name} exited. "
                f"Logs:\n{container_logs}\n"
            )
            sys.stderr.flush()

        ready = _wait_for_port("127.0.0.1", port, timeout=timeout) if container_ok else False
        if ready:
            # A successful TCP connect on a published port only proves that
            # docker-proxy is up — NOT that the application inside answers
            # (found live: app still waiting on its database passed the port
            # check, traffic then failed 118/118). Readiness requires an
            # actual HTTP response; any status code counts (even 500 means
            # the app is alive), so probe "/" rather than a health path the
            # app may not implement.
            ready = _wait_for_http_response(
                f"http://127.0.0.1:{port}/",
                timeout=timeout,
                label=f"service '{svc.compose_service_name}' on :{port}",
            )
            if not ready:
                logger.warning(
                    "Service %s: port %d accepts connections but the application "
                    "never answered HTTP within %ds (still starting? waiting on its database?).",
                    svc.compose_service_name, port, timeout,
                )

        deployed = DeployedService(
            service=svc,
            port=port,
            ready=ready,
        )
        result.services.append(deployed)

    # Verify Jaeger started (added to compose override as ``container_name``)
    if not _wait_for_port("127.0.0.1", jaeger_port, timeout=timeout):
        raise AnalysisError(
            code=ErrorCode.JAEGER_NOT_READY,
            _override_detail=f"Jaeger port {jaeger_port} not listening after {timeout}s.",
            recoverable=True,
        )

    try:
        r = requests.get(f"http://127.0.0.1:{jaeger_port}/api/services", timeout=10)
        if r.status_code != 200:
            raise AnalysisError(
                code=ErrorCode.JAEGER_NOT_READY,
                _override_detail=f"Jaeger API returned status {r.status_code}",
                recoverable=True,
            )
    except requests.RequestException as e:
        raise AnalysisError(
            code=ErrorCode.JAEGER_NOT_READY,
            _override_detail=f"Jaeger health check failed: {e}",
            recoverable=True,
        )

    # Fallback: ensure Jaeger is on the compose network (in case the early
    # poll missed it, or the project uses a custom network name).
    if jaeger_container_name and include_jaeger is False:
        _connect_jaeger_to_compose_network(
            compose_file, override_file, jaeger_container_name, project,
        )

    return result


def _remove_override_file(override_file: Path) -> None:
    try:
        if override_file.exists():
            override_file.unlink()
    except OSError:
        pass


def cleanup_docker_compose(
    project: ProjectInfo,
    container_name: str = "mba-jaeger",
) -> list[AnalysisError]:
    errors: list[AnalysisError] = []

    if not _docker_installed():
        return errors
    if not _docker_daemon_ready():
        return errors

    compose_file = _find_compose_file(project.root_dir)
    if compose_file is None:
        return errors

    override_file = project.root_dir / ".mba-compose-override.yml"

    try:
        cmd = ["docker", "compose", "-f", str(compose_file)]
        if override_file.exists():
            cmd.extend(["-f", str(override_file)])
        cmd.extend(["down", "--remove-orphans"])

        subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except subprocess.TimeoutExpired:
        logger.warning("Docker compose down timed out — skipping")
    except FileNotFoundError:
        pass

    # Split-brain guard: a leftover Jaeger created by a PREVIOUS run's
    # include_jaeger=True override carries the compose service alias
    # "mba-jaeger" on the project networks. If a later run reuses a
    # standalone Jaeger instead (include_jaeger=False), application spans
    # resolve "mba-jaeger" to the LEFTOVER container while collection
    # queries the standalone one on localhost:16686 — two Jaegers, each
    # holding half the truth, and the analysis silently sees zero
    # application spans (observed live across three full runs).
    try:
        project_name = _get_compose_project_name(compose_file)
        ps = subprocess.run(
            [
                "docker", "ps", "-aq",
                "--filter", f"label=com.docker.compose.project={project_name}",
                "--filter", f"label=com.docker.compose.service={container_name}",
            ],
            capture_output=True, text=True, timeout=15,
        )
        leftover_ids = ps.stdout.split()
        if leftover_ids:
            logger.info(
                "Removing %d leftover compose-managed Jaeger container(s) from a previous run "
                "(prevents split-brain trace collection)", len(leftover_ids),
            )
            subprocess.run(["docker", "rm", "-f", *leftover_ids], capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        if override_file.exists():
            override_file.unlink()
    except OSError:
        pass

    for otel_df in find_otel_dockerfiles(project.root_dir):
        try:
            otel_df.unlink()
        except OSError:
            pass

    return errors


def cleanup_services(deployment: DeploymentResult) -> list[AnalysisError]:
    errors: list[AnalysisError] = []

    for svc in deployment.services:
        if svc.process and svc.pid:
            _kill_process_tree(svc.process)

    return errors
