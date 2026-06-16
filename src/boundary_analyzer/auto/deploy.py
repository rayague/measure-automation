from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml

from boundary_analyzer.auto.errors import AnalysisError, ErrorCode
from boundary_analyzer.auto.models import ProjectInfo, ServiceInfo

logger = logging.getLogger(__name__)


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
            if resp.status_code < 500:
                return True
        except (requests.RequestException, ConnectionError):
            pass
        time.sleep(interval)
    return False


def _docker_available() -> bool:
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def start_jaeger(
    jaeger_port: int = 16686,
    otlp_port: int = 4318,
    container_name: str = "mba-jaeger",
    timeout: int = 30,
) -> int:
    if _is_port_in_use(jaeger_port):
        try:
            r = requests.get(f"http://127.0.0.1:{jaeger_port}/api/services", timeout=5)
            if r.status_code == 200:
                return jaeger_port
        except requests.RequestException:
            pass
        raise AnalysisError(
            code=ErrorCode.DOCKER_PORT_CONFLICT,
            scope=f"port {jaeger_port}",
            _override_detail=f"Port {jaeger_port} (Jaeger UI) is already in use by another process.",
            recoverable=True,
        )

    if not _docker_available():
        raise AnalysisError(
            code=ErrorCode.DOCKER_NOT_FOUND,
            recoverable=True,
        )

    try:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        pass

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

    if not _wait_for_port("127.0.0.1", jaeger_port, timeout=timeout):
        raise AnalysisError(
            code=ErrorCode.JAEGER_NOT_READY,
            _override_detail=f"Container started but port {jaeger_port} not listening after {timeout}s.",
            recoverable=True,
        )

    time.sleep(3)

    try:
        r = requests.get(f"http://127.0.0.1:{jaeger_port}/api/services", timeout=10)
        if r.status_code != 200:
            raise AnalysisError(
                code=ErrorCode.JAEGER_NOT_READY,
                _override_detail=f"Jaeger API returned status {r.status_code}.",
                recoverable=True,
            )
    except requests.RequestException as e:
        raise AnalysisError(
            code=ErrorCode.JAEGER_NOT_READY,
            _override_detail=str(e),
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
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as e:
                logger.warning("Failed to kill process on port %s: %s", port, e)
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


def _find_compose_file(project_dir: Path) -> Path | None:
    for name in ["docker-compose.yml", "docker-compose.yaml"]:
        p = project_dir / name
        if p.exists():
            return p
    return None


def _build_compose_override(
    project: ProjectInfo,
    jaeger_port: int = 16686,
    otlp_port: int = 4318,
    container_name: str = "mba-jaeger",
) -> str:
    override: dict[str, Any] = {
        "services": {
            container_name: {
                "image": "jaegertracing/all-in-one:latest",
                "ports": [
                    f"{jaeger_port}:16686",
                    f"{otlp_port}:4318",
                ],
            },
        },
    }

    for svc in project.services:
        if not svc.compose_service_name:
            continue

        env = [
            f"OTEL_SERVICE_NAME={svc.name}",
            f"OTEL_EXPORTER_OTLP_ENDPOINT=http://{container_name}:4317",
        ]

        svc_config: dict[str, Any] = {
            "environment": env,
            "depends_on": {
                container_name: {"condition": "service_started"},
            },
        }

        if svc.language == "python":
            env.extend(
                [
                    "OTEL_PYTHON_CONFIGURATOR=opentelemetry-sdk-configurator",
                    "OTEL_METRICS_EXPORTER=none",
                    "OTEL_LOGS_EXPORTER=none",
                ]
            )

        elif svc.language == "java":
            agent_host = _ensure_java_agent()
            if agent_host:
                env.append(f"JAVA_TOOL_OPTIONS=-javaagent:/mba-agent/{_AGENT_JAR_NAME}")
                env.append("OTEL_METRICS_EXPORTER=none")
                env.append("OTEL_LOGS_EXPORTER=none")
                svc_config["volumes"] = [
                    f"{agent_host}:/mba-agent:ro",
                ]

        elif svc.language == "node":
            env.append("NODE_OPTIONS=--require @opentelemetry/auto-instrumentations-node/register")
            env.append("OTEL_METRICS_EXPORTER=none")
            env.append("OTEL_LOGS_EXPORTER=none")

        elif svc.language == "php":
            env.append("OTEL_PHP_AUTOLOAD_ENABLED=true")
            env.append("OTEL_METRICS_EXPORTER=none")
            env.append("OTEL_LOGS_EXPORTER=none")

        elif svc.language == "dotnet":
            env.append("OTEL_DOTNET_AUTO_TRACES_EXPORTER=otlp")
            env.append("OTEL_DOTNET_AUTO_METRICS_EXPORTER=none")
            env.append("OTEL_DOTNET_AUTO_LOGS_EXPORTER=none")
            env.append("OTEL_DOTNET_AUTO_FLUSH_ON_UNHANDLEDEXCEPTION=true")

        override["services"][svc.compose_service_name] = svc_config

    return yaml.dump(override, default_flow_style=False, sort_keys=False)


def deploy_docker_compose(
    project: ProjectInfo,
    jaeger_port: int = 16686,
    otlp_port: int = 4318,
    container_name: str = "mba-jaeger",
    timeout: int = 60,
) -> DeploymentResult:
    compose_file = _find_compose_file(project.root_dir)
    if compose_file is None:
        raise AnalysisError(
            code=ErrorCode.DEPLOY_UNKNOWN,
            scope=str(project.root_dir),
            _override_detail="No docker-compose.yml or docker-compose.yaml found.",
            recoverable=False,
        )

    if not _docker_available():
        raise AnalysisError(
            code=ErrorCode.DOCKER_NOT_FOUND,
            recoverable=True,
        )

    result = DeploymentResult(
        jaeger_port=jaeger_port,
        otlp_port=otlp_port,
    )

    override_yaml = _build_compose_override(project, jaeger_port, otlp_port, container_name)
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

    try:
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
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        _remove_override_file(override_file)
        raise AnalysisError(
            code=ErrorCode.DOCKER_COMPOSE_FAILED,
            _override_detail=e.stderr.strip() or str(e),
            recoverable=True,
        )
    except FileNotFoundError:
        _remove_override_file(override_file)
        raise AnalysisError(
            code=ErrorCode.DOCKER_NOT_FOUND,
            recoverable=True,
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
        ready = _wait_for_port("127.0.0.1", port, timeout=timeout)
        if ready:
            health_url = f"http://127.0.0.1:{port}{svc.health_endpoint}"
            _wait_for_health(health_url, timeout=15)

        deployed = DeployedService(
            service=svc,
            port=port,
            ready=ready,
        )
        result.services.append(deployed)

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
    compose_file = _find_compose_file(project.root_dir)
    if compose_file is None:
        return errors

    override_file = project.root_dir / ".mba-compose-override.yml"

    try:
        cmd = ["docker", "compose", "-f", str(compose_file)]
        if override_file.exists():
            cmd.extend(["-f", str(override_file)])
        cmd.extend(["down", "--remove-orphans"])

        subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=True)
    except subprocess.CalledProcessError as e:
        err = AnalysisError(
            code=ErrorCode.DOCKER_STOP_FAILED,
            _override_detail=e.stderr.strip() or str(e),
        )
        errors.append(err)
    except FileNotFoundError:
        pass

    try:
        if override_file.exists():
            override_file.unlink()
    except OSError:
        pass

    return errors


def cleanup_services(deployment: DeploymentResult) -> list[AnalysisError]:
    errors: list[AnalysisError] = []

    for svc in deployment.services:
        if svc.process and svc.pid:
            try:
                if os.name == "nt":
                    svc.process.terminate()
                else:
                    os.kill(svc.pid, signal.SIGTERM)

                svc.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    svc.process.kill()
                    svc.process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired) as e:
                    logger.warning("Failed to kill service %s: %s", svc.service.name, e)
                    errors.append(
                        AnalysisError(
                            code=ErrorCode.PROCESS_KILL_FAILED,
                            scope=svc.service.name,
                            original=str(e),
                        )
                    )
            except OSError as e:
                logger.warning("Failed to wait for service %s: %s", svc.service.name, e)
                errors.append(
                    AnalysisError(
                        code=ErrorCode.PROCESS_KILL_FAILED,
                        scope=svc.service.name,
                        original=str(e),
                    )
                )

    return errors
