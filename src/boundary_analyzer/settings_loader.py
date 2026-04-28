from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
ENV_SETTINGS_PATH = "BOUNDARY_ANALYZER_SETTINGS"


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

    return data if isinstance(data, dict) else {}
