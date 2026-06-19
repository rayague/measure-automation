from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

"""Shared utility helpers for the boundary analyzer pipeline."""

logger = logging.getLogger(__name__)


def save_csv(df: pd.DataFrame, path: Path) -> None:
    """Save a DataFrame to CSV, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_csv(path, index=False)
    except (OSError, PermissionError) as exc:
        logger.error("Failed to save CSV to %s: %s", path, exc)
        raise
