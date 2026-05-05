from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
ENV_SETTINGS_PATH = "BOUNDARY_ANALYZER_SETTINGS"
ENV_OUTPUT_DIR = "BOUNDARY_ANALYZER_OUTPUT_DIR"
ENV_DATA_DIR = "BOUNDARY_ANALYZER_DATA_DIR"
ENV_REPORTS_DIR = "BOUNDARY_ANALYZER_REPORTS_DIR"


def get_data_dir() -> Path:
    value = os.environ.get(ENV_DATA_DIR, "").strip()
    return Path(value) if value else Path("data")


def get_reports_dir() -> Path:
    value = os.environ.get(ENV_REPORTS_DIR, "").strip()
    return Path(value) if value else Path("reports")


def get_settings_path(explicit_path: str | Path | None = None) -> Path:
    """Resolve settings path.

    Priority:
    1) explicit_path argument
    2) environment variable BOUNDARY_ANALYZER_SETTINGS
    3) default config/settings.yaml
    """
    if explicit_path is not None:
        return Path(explicit_path)

    env_value = os.environ.get(ENV_SETTINGS_PATH, "").strip()
    if env_value:
        return Path(env_value)

    return DEFAULT_SETTINGS_PATH


def load_settings(explicit_path: str | Path | None = None) -> dict[str, Any]:
    """Load YAML settings.

    Returns an empty dict if the file does not exist.
    """
    settings_path = get_settings_path(explicit_path)
    if not settings_path.exists():
        return {}

    with settings_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    settings: dict[str, Any] = data if isinstance(data, dict) else {}

    output_dir_override = os.environ.get(ENV_OUTPUT_DIR, "").strip()
    if output_dir_override:
        settings["output_dir"] = output_dir_override

    return settings


def get_traces_dir(settings: dict[str, Any] | None = None) -> Path:
    """Resolve the traces directory consistently.

    Priority:
    1) BOUNDARY_ANALYZER_OUTPUT_DIR environment variable
    2) BOUNDARY_ANALYZER_DATA_DIR / "raw" / "traces"
    3) settings.yaml → output_dir
    4) default: data/raw/traces
    """
    # 1) Explicit output dir override (set by --output-dir or --new-dir)
    env_output = os.environ.get(ENV_OUTPUT_DIR, "").strip()
    if env_output:
        return Path(env_output)

    # 2) Data dir override (set by --new-dir or --data-dir)
    env_data = os.environ.get(ENV_DATA_DIR, "").strip()
    if env_data:
        return Path(env_data) / "raw" / "traces"

    # 3) settings.yaml output_dir
    if settings is not None:
        output_dir = settings.get("output_dir", "")
        if output_dir:
            return Path(str(output_dir))

    # 4) Default
    return Path("data/raw/traces")


def clean_data_dirs(
    data_dir: Path | None = None,
    clean_traces: bool = True,
    clean_interim: bool = True,
    clean_processed: bool = True,
) -> dict[str, int]:
    """Safely remove old data files before a new analysis run.

    Returns a dict with counts of deleted files per directory.
    Only deletes known file types (.json, .csv) to avoid accidents.
    """
    base = data_dir or get_data_dir()
    deleted: dict[str, int] = {"traces": 0, "interim": 0, "processed": 0}

    if clean_traces:
        traces_dir = get_traces_dir()
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
