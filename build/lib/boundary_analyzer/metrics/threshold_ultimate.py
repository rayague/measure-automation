from __future__ import annotations

import numpy as np
import pandas as pd

"""Threshold computation methods (percentile, Z-score, fixed) for flagging suspicious services."""


def compute_percentile_threshold(
    scom_scores: pd.Series,
    percentile: float = 25.0,
) -> float:
    """Compute a threshold from the given percentile of SCOM scores.

    Args:
        scom_scores: Series of SCOM scores
        percentile: Percentile value (0-100)

    Returns:
        Score value at the requested percentile
    """
    if scom_scores.empty:
        return 0.0

    threshold = np.percentile(scom_scores, percentile)
    return float(threshold)


def compute_zscore_threshold(
    scom_scores: pd.Series,
    zscore_threshold: float = -1.5,
) -> float:
    """Compute a threshold based on a Z-score cutoff from the mean.

    Args:
        scom_scores: Series of SCOM scores
        zscore_threshold: Z-score cutoff (default -1.5)

    Returns:
        Threshold value = mean + zscore_threshold * std
    """
    if scom_scores.empty or scom_scores.std() == 0:
        return 0.0

    mean = scom_scores.mean()
    std = scom_scores.std()

    # Convert Z-score threshold to actual SCOM threshold
    threshold = mean + (zscore_threshold * std)
    return float(threshold)


def compute_fixed_threshold(
    scom_scores: pd.Series,
    fixed_value: float = 0.5,
) -> float:
    """Return a constant threshold value (interface-compatible fallback).

    Args:
        scom_scores: Unused, present for interface consistency
        fixed_value: Fixed threshold value

    Returns:
        The fixed threshold value
    """
    return fixed_value


def apply_threshold(
    df: pd.DataFrame,
    threshold_method: str = "percentile",
    threshold_percentile: float = 25.0,
    threshold_zscore: float = -1.5,
    fixed_threshold: float = 0.5,
) -> pd.DataFrame:
    """Apply a threshold method and flag suspicious services.

    Adds columns: ``threshold_value``, ``threshold_method``, ``is_suspicious``.

    Args:
        df: DataFrame with a ``scom_score`` column
        threshold_method: ``"percentile"``, ``"zscore"``, or ``"fixed"``
        threshold_percentile: Percentile for percentile method
        threshold_zscore: Z-score cutoff for zscore method
        fixed_threshold: Fixed value for fixed method

    Returns:
        DataFrame with threshold columns added
    """
    if df.empty:
        df["threshold_value"] = 0.0
        df["threshold_method"] = threshold_method
        df["is_suspicious"] = False
        return df

    scom_scores = df["scom_score"]

    # Compute threshold based on method
    if threshold_method == "percentile":
        threshold = compute_percentile_threshold(scom_scores, threshold_percentile)
    elif threshold_method == "zscore":
        threshold = compute_zscore_threshold(scom_scores, threshold_zscore)
    elif threshold_method == "fixed":
        threshold = compute_fixed_threshold(scom_scores, fixed_threshold)
    else:
        raise ValueError(f"Unknown threshold method: {threshold_method}")

    # Apply threshold
    df["threshold_value"] = threshold
    df["threshold_method"] = threshold_method
    df["is_suspicious"] = df["scom_score"] < threshold

    return df
