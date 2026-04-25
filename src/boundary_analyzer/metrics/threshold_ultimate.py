from __future__ import annotations

import numpy as np
import pandas as pd


def compute_percentile_threshold(
    scom_scores: pd.Series,
    percentile: float = 25.0,
) -> float:
    """Compute threshold based on percentile.
    
    Services below this percentile are considered suspicious.
    Example: percentile=25 means bottom 25% are suspicious.
    
    Args:
        scom_scores: Series of SCOM scores
        percentile: Percentile value (0-100), default 25
    
    Returns:
        Threshold value (SCOM score below this is suspicious)
    """
    if scom_scores.empty:
        return 0.0
    
    threshold = np.percentile(scom_scores, percentile)
    return float(threshold)


def compute_zscore_threshold(
    scom_scores: pd.Series,
    zscore_threshold: float = -1.5,
) -> float:
    """Compute threshold based on Z-score.
    
    Services with Z-score below this threshold are suspicious.
    Z-score = (score - mean) / std
    Example: zscore_threshold=-1.5 means below -1.5 standard deviations is suspicious.
    
    Args:
        scom_scores: Series of SCOM scores
        zscore_threshold: Z-score cutoff, default -1.5
    
    Returns:
        Threshold value (SCOM score below this is suspicious)
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
    """Return fixed threshold value (for comparison/fallback).
    
    Args:
        scom_scores: Series of SCOM scores (not used, kept for interface consistency)
        fixed_value: Fixed threshold value, default 0.5
    
    Returns:
        Fixed threshold value
    """
    return fixed_value


def apply_threshold(
    df: pd.DataFrame,
    threshold_method: str = "percentile",
    threshold_percentile: float = 25.0,
    threshold_zscore: float = -1.5,
    fixed_threshold: float = 0.5,
) -> pd.DataFrame:
    """Apply threshold to flag suspicious services.
    
    Adds columns:
    - threshold_value: The computed threshold
    - threshold_method: Which method was used
    - is_suspicious: Boolean flag (True if SCOM < threshold)
    
    Args:
        df: DataFrame with scom_score column
        threshold_method: "percentile", "zscore", or "fixed"
        threshold_percentile: Percentile value (0-100) for percentile method
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
