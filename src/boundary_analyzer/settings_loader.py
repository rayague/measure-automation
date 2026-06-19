from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)


DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
ENV_SETTINGS_PATH = "BOUNDARY_ANALYZER_SETTINGS"
ENV_OUTPUT_DIR = "BOUNDARY_ANALYZER_OUTPUT_DIR"
ENV_DATA_DIR = "BOUNDARY_ANALYZER_DATA_DIR"
ENV_REPORTS_DIR = "BOUNDARY_ANALYZER_REPORTS_DIR"


class LlmSettings(BaseModel):
    enabled: bool = False
    model: str = "qwen/qwen3-coder:free"


class Settings(BaseModel):
    jaeger_base_url: str = "http://localhost:16686"
    service_name: str = ""
    lookback_minutes: int = 10
    limit_traces: int = 20
    endpoint_weighting: bool = True
    scom_threshold: float = 0.5
    threshold_method: str = "percentile"
    threshold_percentile: float = 25.0
    threshold_zscore: float = -1.5
    output_dir: str = str(Path("data") / "raw" / "traces")
    llm: LlmSettings = LlmSettings()


def get_data_dir() -> Path:
    value = os.environ.get(ENV_DATA_DIR, "").strip()
    return Path(value) if value else Path("data")


def get_reports_dir() -> Path:
    value = os.environ.get(ENV_REPORTS_DIR, "").strip()
    return Path(value) if value else Path("reports")


def get_settings_path(explicit_path: str | Path | None = None) -> Path:
    if explicit_path is not None:
        return Path(explicit_path)

    env_value = os.environ.get(ENV_SETTINGS_PATH, "").strip()
    if env_value:
        return Path(env_value)

    return DEFAULT_SETTINGS_PATH


def load_settings(explicit_path: str | Path | None = None) -> Settings:
    settings_path = get_settings_path(explicit_path)
    raw: dict[str, Any] = {}
    if settings_path.exists():
        with settings_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        raw = data if isinstance(data, dict) else {}

    output_dir_override = os.environ.get(ENV_OUTPUT_DIR, "").strip()
    if output_dir_override:
        raw["output_dir"] = output_dir_override

    return Settings(**raw)


def get_traces_dir(settings: Settings | None = None) -> Path:
    env_output = os.environ.get(ENV_OUTPUT_DIR, "").strip()
    if env_output:
        return Path(env_output)

    env_data = os.environ.get(ENV_DATA_DIR, "").strip()
    if env_data:
        return Path(env_data) / "raw" / "traces"

    if settings is not None and settings.output_dir:
        return Path(settings.output_dir)

    return get_data_dir() / "raw" / "traces"


def clean_data_dirs(
    data_dir: Path | None = None,
    clean_traces: bool = True,
    clean_interim: bool = True,
    clean_processed: bool = True,
) -> dict[str, int]:
    base = data_dir or get_data_dir()
    deleted: dict[str, int] = {"traces": 0, "interim": 0, "processed": 0}

    if clean_traces:
        # When a data_dir is explicitly provided, use it; otherwise fall back to the env-configured path
        traces_dir = (base / "raw" / "traces") if data_dir is not None else get_traces_dir()
        if traces_dir.exists() and traces_dir.is_dir():
            for p in traces_dir.glob("*.json"):
                try:
                    p.unlink()
                    deleted["traces"] += 1
                except OSError:
                    pass

    if clean_interim:
        interim_dir = base / "interim"
        if interim_dir.exists() and interim_dir.is_dir():
            for p in interim_dir.glob("*.csv"):
                try:
                    p.unlink()
                    deleted["interim"] += 1
                except OSError:
                    pass

    if clean_processed:
        processed_dir = base / "processed"
        if processed_dir.exists() and processed_dir.is_dir():
            for p in processed_dir.glob("*.csv"):
                try:
                    p.unlink()
                    deleted["processed"] += 1
                except OSError:
                    pass

    return deleted


def get_llm_enabled(settings: Settings | None = None) -> bool:
    if settings is None:
        settings = load_settings()

    env_override = os.environ.get("BOUNDARY_ANALYZER_LLM_ENABLED", "").strip()
    if env_override == "1":
        return True

    if not settings.llm.enabled:
        return False

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    return bool(api_key)
