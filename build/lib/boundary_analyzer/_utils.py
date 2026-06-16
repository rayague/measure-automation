from __future__ import annotations

from pathlib import Path

import pandas as pd

"""Shared utility helpers for the boundary analyzer pipeline."""


def save_csv(df: pd.DataFrame, path: Path) -> None:
    """Save a DataFrame to CSV, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
