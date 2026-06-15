from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from boundary_analyzer.auto.models import DetectionResult, EntryPoint

logger = logging.getLogger(__name__)


@dataclass
class Instrumentation:
    env_vars: dict[str, str] = field(default_factory=dict)
    files_to_install: list[str] = field(default_factory=list)
    need_build: bool = False


class LanguagePlugin(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def detect(self, root: Path) -> DetectionResult: ...

    @abstractmethod
    def find_entry_points(self, root: Path) -> list[EntryPoint]: ...

    @abstractmethod
    def detect_framework(self, root: Path, entry: EntryPoint) -> str: ...

    @abstractmethod
    def instrument(self, entry: EntryPoint, service_name: str, otlp_endpoint: str) -> Instrumentation: ...

    @abstractmethod
    def run_command(self, entry: EntryPoint, port: int | None = None) -> list[str] | None: ...

    @abstractmethod
    def install_command(self, root: Path) -> list[str] | None: ...

    @abstractmethod
    def guess_port(self, entry: EntryPoint) -> int | None: ...

    @abstractmethod
    def has_openapi(self) -> bool: ...

    @abstractmethod
    def openapi_paths(self) -> list[str]: ...
