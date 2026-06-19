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


def classify_scom(value: float | int | str | None) -> str:
    """Classify a SCOM score into a human-readable cohesion label.

    Thresholds:
        >= 0.8  → Très cohésif
        >= 0.5  → Cohésif
        >= 0.3  → Peu cohésif
        <  0.3  → Pas cohésif
        NaN/None → N/A
    """
    if value is None:
        return "N/A"
    try:
        v = float(value)
    except (ValueError, TypeError):
        return "N/A"
    if pd.isna(v):
        return "N/A"
    if v >= 0.8:
        return "Très cohésif"
    if v >= 0.5:
        return "Cohésif"
    if v >= 0.3:
        return "Peu cohésif"
    return "Pas cohésif"
