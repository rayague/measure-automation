from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

"""Error codes and exception types for the automatic analysis pipeline.

Defines ``ErrorCode`` (enum of typed error identifiers), ``AnalysisError``
(exception with user-facing messages and fix suggestions), and helper
factory functions.
"""

logger = logging.getLogger(__name__)


class ErrorCode(Enum):
    """Enumeration of typed error codes across all pipeline stages.

    Each code has an associated user-facing message, detail, fix suggestion,
    and fatal flag defined in the ``_ERROR_MESSAGES`` dict.
    """

    LANG_NOT_FOUND = "discover.lang.NOT_FOUND"
    LANG_UNSUPPORTED = "discover.lang.UNSUPPORTED"
    LANG_AMBIGUOUS = "discover.lang.AMBIGUOUS"
    FRAMEWORK_UNKNOWN = "discover.framework.UNKNOWN"
    FRAMEWORK_UNSUPPORTED = "discover.framework.UNSUPPORTED"
    FRAMEWORK_AMBIGUOUS = "discover.framework.AMBIGUOUS"
    ENTRY_NOT_FOUND = "discover.entry.NOT_FOUND"
    ENTRY_AMBIGUOUS = "discover.entry.AMBIGUOUS"
    ENTRY_MALFORMED = "discover.entry.MALFORMED"
    PORT_NOT_FOUND = "discover.port.NOT_FOUND"
    PORT_AMBIGUOUS = "discover.port.AMBIGUOUS"
    DEPLOY_UNKNOWN = "discover.deploy.UNKNOWN"
    PROJECT_EMPTY = "discover.project.EMPTY"
    PROJECT_PERMISSION = "discover.project.PERMISSION_DENIED"

    DOCKER_NOT_FOUND = "deploy.docker.NOT_FOUND"
    DOCKER_DAEMON_DOWN = "deploy.docker.DAEMON_DOWN"
    DOCKER_PERMISSION = "deploy.docker.PERMISSION_DENIED"
    DOCKER_PORT_CONFLICT = "deploy.docker.PORT_CONFLICT"
    DOCKER_PULL_FAILED = "deploy.docker.PULL_FAILED"
    DOCKER_START_FAILED = "deploy.docker.START_FAILED"
    DOCKER_BUILD_FAILED = "deploy.docker.BUILD_FAILED"
    DOCKER_COMPOSE_FAILED = "deploy.docker.COMPOSE_FAILED"
    JAEGER_NOT_READY = "deploy.jaeger.NOT_READY"
    PIP_NOT_FOUND = "deploy.python.PIP_NOT_FOUND"
    PIP_INSTALL_FAILED = "deploy.python.INSTALL_FAILED"
    PIP_PEP668 = "deploy.python.PEP_668"
    ENTRY_FAILED = "deploy.start.ENTRY_FAILED"
    PORT_BIND_FAILED = "deploy.start.PORT_BIND_FAILED"
    HEALTH_TIMEOUT = "deploy.health.TIMEOUT"
    ALL_SERVICES_FAILED = "deploy.start.ALL_FAILED"

    SERVICE_UNREACHABLE = "traffic.connect.UNREACHABLE"
    OPENAPI_NOT_FOUND = "traffic.discovery.OPENAPI_NOT_FOUND"
    OPENAPI_MALFORMED = "traffic.discovery.OPENAPI_MALFORMED"
    AST_PARSE_FAILED = "traffic.discovery.AST_PARSE_FAILED"
    NO_ENDPOINTS_FOUND = "traffic.discovery.NO_ENDPOINTS"
    ALL_ENDPOINTS_FAILED = "traffic.execute.ALL_FAILED"
    PARTIAL_ENDPOINTS_FAILED = "traffic.execute.PARTIAL"
    ALL_400 = "traffic.execute.ALL_400"
    AUTH_REQUIRED = "traffic.auth.REQUIRED"
    AUTH_FAILED = "traffic.auth.FAILED"
    RATE_LIMITED = "traffic.execute.RATE_LIMITED"

    JAEGER_UNREACHABLE = "collect.connect.UNREACHABLE"
    NO_TRACES = "collect.data.NO_TRACES"
    ONLY_HEALTH_TRACES = "collect.data.ONLY_HEALTH"
    TRACE_IO_ERROR = "collect.export.IO_ERROR"

    PIPELINE_FAILED = "analyze.pipeline.FAILED"
    NO_ENDPOINTS_IN_TRACES = "analyze.pipeline.NO_ENDPOINTS"
    NO_DB_IN_TRACES = "analyze.pipeline.NO_DB"
    SCOM_FAILED = "analyze.scom.FAILED"
    REPORT_FAILED = "analyze.report.FAILED"

    PROCESS_KILL_FAILED = "cleanup.process.KILL_FAILED"
    DOCKER_STOP_FAILED = "cleanup.docker.STOP_FAILED"
    FILE_CLEANUP_FAILED = "cleanup.files.CLEANUP_FAILED"


_ERROR_MESSAGES: dict[ErrorCode, tuple[str, str, str, bool]] = {
    ErrorCode.LANG_NOT_FOUND: (
        "No supported programming language detected in the project.",
        "Searched for: requirements.txt, pyproject.toml, pom.xml, build.gradle, package.json, go.mod, Cargo.toml, *.csproj, composer.json",
        "MBA supports Python, Java, Node.js, .NET, and PHP. Make sure your project has a standard build file.",
        True,
    ),
    ErrorCode.LANG_UNSUPPORTED: (
        "Detected language is not yet supported by MBA.",
        "Found evidence of the language but no plugin is available.",
        "MBA currently supports: Python, Java, Node.js, .NET, PHP. Contribution guide: https://github.com/rayague/measure-automation/wiki/Adding-Plugins",
        True,
    ),
    ErrorCode.LANG_AMBIGUOUS: (
        "Multiple languages detected in the project.",
        "Found build files for more than one language (e.g., pom.xml + package.json).",
        "Specify the language manually with --lang python or --lang java.",
        False,
    ),
    ErrorCode.FRAMEWORK_UNKNOWN: (
        "Framework could not be identified from the source code.",
        "Searched imports for: fastapi, flask, django, starlette, tornado, aiohttp.",
        "MBA will proceed with generic Python instrumentation. Use --framework to force a specific framework.",
        False,
    ),
    ErrorCode.FRAMEWORK_UNSUPPORTED: (
        "Detected framework is not yet supported by MBA.",
        "The framework version or variant is too old or unknown.",
        "MBA supports FastAPI 0.60+, Flask 2.0+, Django 3.2+. Update your framework or use --framework to override detection.",
        False,
    ),
    ErrorCode.FRAMEWORK_AMBIGUOUS: (
        "Multiple frameworks detected in the same project.",
        "Found imports for: fastapi and flask in the same project.",
        "Specify the framework with --framework fastapi or --framework flask.",
        False,
    ),
    ErrorCode.ENTRY_NOT_FOUND: (
        "Could not find the application entry point.",
        "Searched for: main.py, app.py, manage.py, run.py, server.py, application.py, wsgi.py.",
        "Specify the entry point with --entry main.py.",
        False,
    ),
    ErrorCode.ENTRY_AMBIGUOUS: (
        "Multiple entry points found.",
        "Found main.py, app.py, and manage.py. Uncertain which one to use.",
        "Specify the entry point with --entry main.py.",
        False,
    ),
    ErrorCode.ENTRY_MALFORMED: (
        "The entry point file contains syntax errors.",
        "Python cannot parse the file.",
        "Fix the syntax error in the entry point file and run again.",
        False,
    ),
    ErrorCode.PORT_NOT_FOUND: (
        "Could not determine which port the application listens on.",
        "Searched config files, .env, docker-compose.yml for port declarations.",
        "Specify the port with --port 8000.",
        False,
    ),
    ErrorCode.PORT_AMBIGUOUS: (
        "Multiple ports detected. Uncertain which one is correct.",
        "Found port declarations: 8000 (config), 8080 (docker-compose), 5000 (.env).",
        "Specify the port with --port 8000.",
        False,
    ),
    ErrorCode.DEPLOY_UNKNOWN: (
        "Could not determine how to deploy the project.",
        "No Dockerfile, docker-compose.yml, or recognized entry point found.",
        "Add a Dockerfile or a main.py/app.py entry point.",
        False,
    ),
    ErrorCode.PROJECT_EMPTY: (
        "The project directory is empty.",
        "No files found in the specified directory.",
        "Make sure you are pointing to a valid project directory.",
        True,
    ),
    ErrorCode.PROJECT_PERMISSION: (
        "Permission denied when reading the project directory.",
        "The tool does not have read access to some files or directories.",
        "Check file permissions and run again.",
        True,
    ),
    ErrorCode.DOCKER_NOT_FOUND: (
        "Docker is required but was not found on your system.",
        "Checked PATH for: docker.exe, podman.exe, nerdctl.exe",
        "Install Docker Desktop:\n  Windows: winget install Docker.DockerDesktop\n  macOS: brew install --cask docker\n  Linux: sudo apt install docker.io",
        True,
    ),
    ErrorCode.DOCKER_DAEMON_DOWN: (
        "Docker is installed but the daemon is not running.",
        "Checked: docker info returned connection refused.",
        "Start Docker Desktop and wait for the daemon to be ready.",
        True,
    ),
    ErrorCode.DOCKER_PERMISSION: (
        "Permission denied when accessing Docker.",
        "Current user does not have permission to use Docker.",
        "On Linux: sudo usermod -aG docker $USER && newgrp docker\nOn Windows: add user to docker-users group.",
        True,
    ),
    ErrorCode.DOCKER_PORT_CONFLICT: (
        "A port required by Jaeger is already in use.",
        "Checked ports: 16686 (Jaeger UI), 4318 (OTLP HTTP), 4317 (OTLP gRPC).",
        "Use --jaeger-port 16687 to use a different port, or stop the service using the conflicting port.",
        True,
    ),
    ErrorCode.DOCKER_PULL_FAILED: (
        "Failed to pull the Jaeger Docker image.",
        "docker pull jaegertracing/all-in-one:latest failed.",
        "Check your internet connection, or pull manually: docker pull jaegertracing/all-in-one:1.60",
        True,
    ),
    ErrorCode.DOCKER_START_FAILED: (
        "Failed to start the Jaeger Docker container.",
        "docker start mba-jaeger failed.",
        "Check container logs: docker logs mba-jaeger\nOr remove and recreate: docker rm -f mba-jaeger",
        True,
    ),
    ErrorCode.DOCKER_BUILD_FAILED: (
        "Docker build failed for the project.",
        "docker build returned a non-zero exit code.",
        "Check the Dockerfile for errors and try building manually: docker build .",
        True,
    ),
    ErrorCode.DOCKER_COMPOSE_FAILED: (
        "Docker Compose failed to start services.",
        "docker compose up -d returned a non-zero exit code.",
        "Check docker-compose.yml syntax and try manually: docker compose up -d",
        True,
    ),
    ErrorCode.JAEGER_NOT_READY: (
        "Jaeger started but the API is not responding.",
        "Jaeger container is running but GET /api/services timed out after 30 seconds.",
        "Check Jaeger logs: docker logs mba-jaeger\nSometimes Jaeger needs more time to initialize. Try increasing the timeout.",
        True,
    ),
    ErrorCode.PIP_NOT_FOUND: (
        "pip is not installed for the detected Python version.",
        "Checked: pip --version returned command not found.",
        "Install pip: python -m ensurepip --upgrade",
        True,
    ),
    ErrorCode.PIP_INSTALL_FAILED: (
        "Failed to install OpenTelemetry packages via pip.",
        "pip install opentelemetry-sdk opentelemetry-exporter-otlp failed.",
        "Check internet connection. If behind a proxy, set HTTP_PROXY env var.\nIf packages are already installed, use --no-install to skip.",
        True,
    ),
    ErrorCode.PIP_PEP668: (
        "pip refused to install packages due to PEP 668 protection.",
        "Python >=3.11 on some Linux distros blocks system-wide pip installs.",
        "Use --break-system-packages flag or create a virtual environment:\n  python -m venv .venv && .venv\\Scripts\\activate",
        True,
    ),
    ErrorCode.ENTRY_FAILED: (
        "The application failed to start.",
        "The process exited with a non-zero return code.",
        "Run the application manually to see the error:\n  python main.py\nCheck for missing dependencies or environment variables.",
        False,
    ),
    ErrorCode.PORT_BIND_FAILED: (
        "The application could not bind to the detected port.",
        "Port is already in use by another process.",
        "Find and stop the process using the port:\n  netstat -ano | findstr :PORT\nor use --port to specify a different port.",
        False,
    ),
    ErrorCode.HEALTH_TIMEOUT: (
        "The application started but did not become healthy within the timeout.",
        "Health endpoint did not return 200 after 30 seconds.",
        "Check if the application is truly ready. Try accessing the health endpoint manually.\nSome applications need a database or other services to be ready first.",
        False,
    ),
    ErrorCode.ALL_SERVICES_FAILED: (
        "All services failed to start. Nothing to analyze.",
        "Every service deployment attempt failed.",
        "Fix the most critical issue first (see errors above) and run again.",
        True,
    ),
    ErrorCode.SERVICE_UNREACHABLE: (
        "Service is not reachable for traffic generation.",
        "Connection refused when trying to reach the service.",
        "Check that the service is running on the expected host and port.",
        True,
    ),
    ErrorCode.OPENAPI_NOT_FOUND: (
        "OpenAPI/Swagger specification not found.",
        "Checked: GET /openapi.json, /swagger.json, /swagger/v1/swagger.json, /api/openapi.json all returned 404.",
        "Falling back to AST-based endpoint discovery. This is normal for Flask/Django apps that don't expose OpenAPI automatically.",
        False,
    ),
    ErrorCode.OPENAPI_MALFORMED: (
        "Found OpenAPI spec but it is invalid or malformed.",
        "JSON parsing failed or the spec does not match the OpenAPI schema.",
        "Fix the OpenAPI specification in your application.\nRun: python -c \"import json; json.load(open('openapi.json'))\" to check validity.",
        False,
    ),
    ErrorCode.AST_PARSE_FAILED: (
        "Failed to parse source code for endpoint discovery.",
        "Python syntax error in one or more source files.",
        "Fix syntax errors in your Python files. Run: python -m py_compile <file> to find errors.",
        False,
    ),
    ErrorCode.NO_ENDPOINTS_FOUND: (
        "No HTTP endpoints could be discovered.",
        "Neither OpenAPI nor AST parsing found any endpoints.",
        "Make sure your application exposes HTTP routes. If it uses gRPC, GraphQL, or WebSockets only, MBA currently supports HTTP REST endpoints.",
        True,
    ),
    ErrorCode.ALL_ENDPOINTS_FAILED: (
        "All discovered endpoints returned errors during traffic generation.",
        "Every HTTP request returned a non-2xx status code.",
        "Check that the application is functioning correctly. Try accessing an endpoint manually in a browser.\nThe last error response was: {detail}",
        False,
    ),
    ErrorCode.PARTIAL_ENDPOINTS_FAILED: (
        "Some endpoints returned errors during traffic generation.",
        "{ok_count}/{total_count} endpoints succeeded.",
        "Failed endpoints: {detail}\nThese endpoints may need specific parameters, authentication, or headers that could not be auto-generated.",
        False,
    ),
    ErrorCode.ALL_400: (
        "All POST/PUT endpoints returned 400 Bad Request.",
        "The generated payloads did not pass server-side validation.",
        "Run with --verbose to see the exact payloads sent.\nConsider using --llm for smarter payload generation.",
        False,
    ),
    ErrorCode.AUTH_REQUIRED: (
        "Authentication is required to access the API.",
        "Detected a /login or /auth endpoint, or all endpoints return 401/403.",
        "MBA can attempt auto-login. If it fails, provide credentials with --username and --password.\nAlternatively, run against a test environment without authentication.",
        False,
    ),
    ErrorCode.AUTH_FAILED: (
        "Authentication attempt failed.",
        "Login endpoint did not accept the provided or auto-generated credentials.",
        "Provide valid credentials with --username and --password.",
        False,
    ),
    ErrorCode.RATE_LIMITED: (
        "The application is rate-limiting our requests.",
        "Received 429 Too Many Requests responses.",
        "Reduce concurrency with --workers 2 or increase delay between requests.",
        False,
    ),
    ErrorCode.JAEGER_UNREACHABLE: (
        "Jaeger is not reachable after traffic generation.",
        "Connection to Jaeger API failed at the collection step.",
        "Jaeger may have crashed. Check: docker ps | findstr mba-jaeger\nIf Jaeger is on a different host, use --jaeger-addr http://host:16686",
        True,
    ),
    ErrorCode.NO_TRACES: (
        "No traces found in Jaeger for the analysis window.",
        "Traffic was generated but Jaeger has no recorded traces.",
        "OpenTelemetry instrumentation is not exporting to Jaeger.\nCheck that OTEL_EXPORTER_OTLP_ENDPOINT is set correctly (default: http://localhost:4318).\nCheck service logs for OTel exporter errors.",
        False,
    ),
    ErrorCode.ONLY_HEALTH_TRACES: (
        "Only health check traces were found in Jaeger.",
        "All traces correspond to /health or /readyz endpoints.",
        "The traffic generator may not have reached real business endpoints.\nCheck that endpoint discovery found the correct routes.",
        False,
    ),
    ErrorCode.TRACE_IO_ERROR: (
        "Failed to write trace data to disk.",
        "IO error when saving Jaeger traces JSON file.",
        "Check disk space and write permissions.\nThe output directory may be on a read-only filesystem.",
        False,
    ),
    ErrorCode.PIPELINE_FAILED: (
        "The SCOM pipeline encountered an error.",
        "One of the pipeline steps raised an unhandled exception.",
        "Run with --verbose for detailed error information.\nThis is likely an internal error. Please report it at https://github.com/rayague/measure-automation/issues",
        False,
    ),
    ErrorCode.NO_ENDPOINTS_IN_TRACES: (
        "No HTTP endpoint spans found in the collected traces.",
        "The traces contain spans but none of them match HTTP endpoint patterns.",
        "The instrumentation may not be capturing HTTP requests properly.\nCheck that the OTel instrumentation is correctly configured for your web framework.",
        False,
    ),
    ErrorCode.NO_DB_IN_TRACES: (
        "No database operations found in the collected traces.",
        "None of the spans contain database operation tags (db.system, db.statement).",
        "SCOM will be 0 for all services (no DB data to compute cohesion).\nThis is normal if the application doesn't use a database.\nIf it does, check OTel DB instrumentation is enabled.",
        False,
    ),
    ErrorCode.SCOM_FAILED: (
        "SCOM computation failed.",
        "The computation produced NaN or division by zero.",
        "This is likely due to insufficient data. Try running with more traffic (--duration 120).",
        False,
    ),
    ErrorCode.REPORT_FAILED: (
        "Failed to generate the analysis report.",
        "The report builder encountered an error.",
        "Run with --verbose for details. This is likely an internal error.",
        False,
    ),
    ErrorCode.PROCESS_KILL_FAILED: (
        "Failed to stop the application process.",
        "The process did not respond to SIGTERM.",
        "Stop the process manually:\n  taskkill /F /PID {detail}\nOr restart your terminal.",
        False,
    ),
    ErrorCode.DOCKER_STOP_FAILED: (
        "Failed to stop the Jaeger Docker container.",
        "docker stop mba-jaeger timed out.",
        "Stop the container manually:\n  docker stop mba-jaeger --time 10\n  docker rm mba-jaeger",
        False,
    ),
    ErrorCode.FILE_CLEANUP_FAILED: (
        "Failed to clean up temporary files.",
        "Some temporary files could not be deleted.",
        "Delete temporary files manually:\n  mba_report/ directory can be safely removed.",
        False,
    ),
}


@dataclass
class AnalysisError(Exception):
    """Pipeline exception with an ``ErrorCode``, scope, and user-facing message/detail/fix."""

    code: ErrorCode
    scope: str = ""
    original: str | None = None
    recoverable: bool = True
    _override_message: str | None = None
    _override_detail: str | None = None
    _override_fix: str | None = None

    def __post_init__(self):
        msg, detail, fix, fatal = _ERROR_MESSAGES.get(self.code, ("Unknown error.", "", "No fix available.", True))
        self._message = self._override_message or msg
        self._detail = self._override_detail or detail
        self._fix = self._override_fix or fix
        if not self.recoverable:
            pass
        if fatal:
            self.recoverable = False

    @property
    def message(self) -> str:
        if self.scope:
            return f"{self._message} [{self.scope}]"
        return self._message

    @property
    def detail(self) -> str:
        return self._detail

    @property
    def fix(self) -> str | None:
        return self._fix

    @property
    def code_str(self) -> str:
        return self.code.value

    def summary(self) -> str:
        lines = [f"[{self.code_str}] {self.message}"]
        if self.detail:
            lines.append(f"  -> {self.detail}")
        if self.fix:
            lines.append(f"  -> Fix: {self.fix}")
        if self.original:
            lines.append(f"  -> Original: {self.original}")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.summary()


def not_found(code: ErrorCode, detail: str = "", scope: str = "") -> AnalysisError:
    """Factory: create an ``AnalysisError`` for a resource-not-found condition."""
    return AnalysisError(code=code, scope=scope, original=detail)


def unexpected(step: str, exc: Exception, scope: str = "") -> AnalysisError:
    """Factory: wrap an unexpected exception into a non-recoverable ``AnalysisError``."""
    return AnalysisError(
        code=ErrorCode.PIPELINE_FAILED,
        scope=f"{step}:{scope}",
        original=f"{exc.__class__.__name__}: {exc}",
        recoverable=False,
    )
