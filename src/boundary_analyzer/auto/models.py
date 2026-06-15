from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

"""Data models for the automatic analysis pipeline.

Defines dataclasses for project discovery, service info, endpoint
descriptions, traffic results, pipeline step results, and the final
analysis report.
"""

if TYPE_CHECKING:
    from boundary_analyzer.auto.errors import AnalysisError


@dataclass
class EntryPoint:
    """A detected application entry point with its framework and optional port."""

    path: Path
    framework: str
    port: int | None = None

    def __str__(self) -> str:
        return str(self.path)


@dataclass
class Endpoint:
    """An HTTP endpoint with method, path, parameters, and authentication metadata."""

    method: str
    path: str
    params: list[dict[str, Any]] = field(default_factory=list)
    request_body: dict[str, Any] | None = None
    auth_required: bool = False
    is_graphql: bool = False
    graphql_field: str = ""
    graphql_args: list[dict[str, Any]] = field(default_factory=list)

    def key(self) -> str:
        return f"{self.method.upper()} {self.path}"

    def __str__(self) -> str:
        return self.key()


@dataclass
class DetectionResult:
    """Result of language/framework detection with confidence score."""

    score: float
    language: str
    framework: str
    entries: list[EntryPoint] = field(default_factory=list)
    has_docker: bool = False
    build_tool: str = ""
    detail: str = ""


@dataclass
class ServiceInfo:
    """Metadata about a single microservice: name, language, framework, ports, and endpoints."""

    name: str
    language: str
    framework: str
    entry_points: list[EntryPoint]
    deployment: str
    ports: list[int] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    health_endpoint: str = "/health"
    endpoints: list[Endpoint] = field(default_factory=list)
    compose_service_name: str = ""

    @property
    def port(self) -> int | None:
        return self.ports[0] if self.ports else None

    def __str__(self) -> str:
        ports = f" :{self.port}" if self.port else ""
        return f"{self.name} ({self.language}/{self.framework}{ports})"


@dataclass
class ProjectInfo:
    """Top-level project info: all services, root directory, language, and framework."""

    services: list[ServiceInfo]
    root_dir: Path
    has_docker: bool = False
    language: str = ""
    framework: str = ""
    plugins_loaded: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return len(self.services) == 0

    @property
    def single_service(self) -> bool:
        return len(self.services) == 1

    def service_by_name(self, name: str) -> ServiceInfo | None:
        for svc in self.services:
            if svc.name == name:
                return svc
        return None


@dataclass
class TrafficResult:
    """Result of traffic generation for a single service."""

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    endpoints_discovered: int = 0
    endpoints_tested: int = 0
    endpoints_ok: int = 0
    duration_seconds: float = 0.0
    auth_used: bool = False
    llm_endpoints: bool = False
    graphql_endpoints: bool = False
    errors: list[Any] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.successful_requests / self.total_requests

    @property
    def all_succeeded(self) -> bool:
        return self.failed_requests == 0 and self.endpoints_tested > 0

    @property
    def none_succeeded(self) -> bool:
        return self.endpoints_ok == 0 and self.endpoints_tested > 0


@dataclass
class StepResult:
    """Result of a single pipeline step (discover, deploy, traffic, etc.)."""

    success: bool
    step_name: str
    message: str = ""
    data: Any = None
    errors: list[AnalysisError] = field(default_factory=list)
    warnings: list[AnalysisError] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    @property
    def status_icon(self) -> str:
        if self.has_errors:
            return "X"
        if self.has_warnings:
            return "!"
        if self.success:
            return "v"
        return "?"

    def merge(self, other: StepResult) -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        if not other.success:
            self.success = False


@dataclass
class AnalysisReport:
    """Complete analysis report: step results, SCOM data, and summary."""

    project: ProjectInfo
    steps: dict[str, StepResult] = field(default_factory=dict)
    scom_results: dict[str, Any] = field(default_factory=dict)
    report_path: Path | None = None
    total_duration_seconds: float = 0.0

    @property
    def all_success(self) -> bool:
        return all(s.success for s in self.steps.values())

    @property
    def has_any_errors(self) -> bool:
        return any(s.has_errors for s in self.steps.values())

    @property
    def has_any_warnings(self) -> bool:
        return any(s.has_warnings for s in self.steps.values())

    def step(self, name: str) -> StepResult | None:
        return self.steps.get(name)

    def all_errors(self) -> list[AnalysisError]:
        result: list[AnalysisError] = []
        for s in self.steps.values():
            result.extend(s.errors)
        return result

    def all_warnings(self) -> list[AnalysisError]:
        result: list[AnalysisError] = []
        for s in self.steps.values():
            result.extend(s.warnings)
        return result
