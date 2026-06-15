from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)


def load_ranking(file_path: Path) -> pd.DataFrame:
    """Load service ranking CSV."""
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    return pd.read_csv(file_path)


def compare_scom_methods(
    simple_path: Path,
    weighted_path: Path,
) -> dict[str, Any]:
    """Compare simple SCOM vs weighted SCOM rankings.

    Returns dict with comparison metrics.
    """
    simple_df = load_ranking(simple_path)
    weighted_df = load_ranking(weighted_path)

    # Merge on service_name
    merged = simple_df[["service_name", "scom_score", "rank"]].merge(
        weighted_df[["service_name", "scom_score", "rank"]],
        on="service_name",
        suffixes=("_simple", "_weighted"),
    )

    # Compute rank correlation
    correlation, p_value = spearmanr(merged["rank_simple"], merged["rank_weighted"])

    # Find services with significant rank changes
    merged["rank_change"] = merged["rank_weighted"] - merged["rank_simple"]
    significant_changes = merged[abs(merged["rank_change"]) > 2]

    # Compare SCOM scores
    merged["scom_change"] = merged["scom_score_weighted"] - merged["scom_score_simple"]

    # Top 5 comparison
    top_5_simple = simple_df.nsmallest(5, "scom_score")["service_name"].tolist()
    top_5_weighted = weighted_df.nsmallest(5, "scom_score")["service_name"].tolist()

    # Bottom 5 comparison
    bottom_5_simple = simple_df.nlargest(5, "scom_score")["service_name"].tolist()
    bottom_5_weighted = weighted_df.nlargest(5, "scom_score")["service_name"].tolist()

    return {
        "rank_correlation": correlation,
        "rank_correlation_p_value": p_value,
        "significant_rank_changes": significant_changes.to_dict("records"),
        "rank_change_stats": {
            "mean": merged["rank_change"].mean(),
            "std": merged["rank_change"].std(),
            "min": merged["rank_change"].min(),
            "max": merged["rank_change"].max(),
        },
        "scom_change_stats": {
            "mean": merged["scom_change"].mean(),
            "std": merged["scom_change"].std(),
            "min": merged["scom_change"].min(),
            "max": merged["scom_change"].max(),
        },
        "top_5_simple": top_5_simple,
        "top_5_weighted": top_5_weighted,
        "bottom_5_simple": bottom_5_simple,
        "bottom_5_weighted": bottom_5_weighted,
        "merged_details": merged,
    }


def compare_threshold_methods(
    fixed_path: Path,
    percentile_path: Path,
    zscore_path: Path | None = None,
) -> dict[str, Any]:
    """Compare fixed, percentile, and z-score threshold methods.

    Returns dict with comparison metrics.
    """
    fixed_df = load_ranking(fixed_path)
    percentile_df = load_ranking(percentile_path)

    # Extract suspicious services
    fixed_suspicious = set(fixed_df[fixed_df["is_suspicious"]]["service_name"])
    percentile_suspicious = set(percentile_df[percentile_df["is_suspicious"]]["service_name"])

    # Get threshold values
    fixed_threshold = fixed_df["threshold_value"].iloc[0] if "threshold_value" in fixed_df.columns else 0.5
    percentile_threshold = (
        percentile_df["threshold_value"].iloc[0] if "threshold_value" in percentile_df.columns else None
    )

    # Compare suspicious lists
    overlap = fixed_suspicious & percentile_suspicious
    only_fixed = fixed_suspicious - percentile_suspicious
    only_percentile = percentile_suspicious - fixed_suspicious

    result = {
        "fixed_threshold": fixed_threshold,
        "percentile_threshold": percentile_threshold,
        "fixed_suspicious_count": len(fixed_suspicious),
        "percentile_suspicious_count": len(percentile_suspicious),
        "overlap_count": len(overlap),
        "only_fixed_count": len(only_fixed),
        "only_percentile_count": len(only_percentile),
        "overlap_services": list(overlap),
        "only_fixed_services": list(only_fixed),
        "only_percentile_services": list(only_percentile),
    }

    # Add z-score comparison if provided
    if zscore_path and zscore_path.exists():
        zscore_df = load_ranking(zscore_path)
        zscore_suspicious = set(zscore_df[zscore_df["is_suspicious"]]["service_name"])
        zscore_threshold = zscore_df["threshold_value"].iloc[0] if "threshold_value" in zscore_df.columns else None

        overlap_zscore = percentile_suspicious & zscore_suspicious
        only_zscore = zscore_suspicious - percentile_suspicious

        result.update(
            {
                "zscore_threshold": zscore_threshold,
                "zscore_suspicious_count": len(zscore_suspicious),
                "percentile_zscore_overlap_count": len(overlap_zscore),
                "only_zscore_count": len(only_zscore),
                "percentile_zscore_overlap_services": list(overlap_zscore),
                "only_zscore_services": list(only_zscore),
            }
        )

    return result


def print_scom_comparison(comparison: dict[str, Any]) -> None:
    """Print SCOM comparison results."""
    logger.info("\n" + "=" * 60)
    logger.info("SCOM METHOD COMPARISON: Simple vs Weighted")
    logger.info("=" * 60)

    logger.info("\nRank Correlation (Spearman): %.4f", comparison["rank_correlation"])
    logger.info("P-value: %.4f", comparison["rank_correlation_p_value"])

    logger.info("\nRank Change Statistics:")
    stats = comparison["rank_change_stats"]
    logger.info("  Mean: %.2f", stats["mean"])
    logger.info("  Std: %.2f", stats["std"])
    logger.info("  Min: %.2f", stats["min"])
    logger.info("  Max: %.2f", stats["max"])

    logger.info("\nSCOM Score Change Statistics:")
    scom_stats = comparison["scom_change_stats"]
    logger.info("  Mean: %.4f", scom_stats["mean"])
    logger.info("  Std: %.4f", scom_stats["std"])
    logger.info("  Min: %.4f", scom_stats["min"])
    logger.info("  Max: %.4f", scom_stats["max"])

    logger.info("\nServices with Significant Rank Changes (|change| > 2):")
    if comparison["significant_rank_changes"]:
        for change in comparison["significant_rank_changes"]:
            logger.info(
                "  %s: %s → %s (change: %+.0f)",
                change["service_name"],
                change["rank_simple"],
                change["rank_weighted"],
                change["rank_change"],
            )
    else:
        logger.info("  None")

    logger.info("\nTop 5 Lowest SCOM (Simple): %s", comparison["top_5_simple"])
    logger.info("Top 5 Lowest SCOM (Weighted): %s", comparison["top_5_weighted"])

    logger.info("\nBottom 5 Highest SCOM (Simple): %s", comparison["bottom_5_simple"])
    logger.info("Bottom 5 Highest SCOM (Weighted): %s", comparison["bottom_5_weighted"])


def print_threshold_comparison(comparison: dict[str, Any]) -> None:
    """Print threshold comparison results."""
    logger.info("\n" + "=" * 60)
    logger.info("THRESHOLD METHOD COMPARISON")
    logger.info("=" * 60)

    logger.info("\nFixed Threshold: %.4f", comparison["fixed_threshold"])
    logger.info("Percentile Threshold: %.4f", comparison["percentile_threshold"])

    logger.info("\nFixed Suspicious Count: %d", comparison["fixed_suspicious_count"])
    logger.info("Percentile Suspicious Count: %d", comparison["percentile_suspicious_count"])

    logger.info("\nOverlap Count: %d", comparison["overlap_count"])
    logger.info("Only Fixed Count: %d", comparison["only_fixed_count"])
    logger.info("Only Percentile Count: %d", comparison["only_percentile_count"])

    if comparison["overlap_services"]:
        logger.info("\nServices flagged by both methods: %s", comparison["overlap_services"])
    if comparison["only_fixed_services"]:
        logger.info("\nServices flagged only by fixed: %s", comparison["only_fixed_services"])
    if comparison["only_percentile_services"]:
        logger.info("\nServices flagged only by percentile: %s", comparison["only_percentile_services"])

    if "zscore_threshold" in comparison:
        logger.info("\nZ-Score Threshold: %.4f", comparison["zscore_threshold"])
        logger.info("Z-Score Suspicious Count: %d", comparison["zscore_suspicious_count"])
        logger.info("Percentile-Z-Score Overlap Count: %d", comparison["percentile_zscore_overlap_count"])
        logger.info("Only Z-Score Count: %d", comparison["only_zscore_count"])


def main() -> int:
    """Run comparison of SCOM and threshold methods."""
    base_dir = Path("data/processed")

    # Check for required files
    simple_path = base_dir / "service_rank_simple.csv"
    weighted_path = base_dir / "service_rank_weighted.csv"

    print("Validation: Comparing SCOM and threshold methods")
    print("=" * 60)

    # Compare SCOM methods if both files exist
    if simple_path.exists() and weighted_path.exists():
        scom_comparison = compare_scom_methods(simple_path, weighted_path)
        print_scom_comparison(scom_comparison)
    else:
        print("\nSCOM comparison skipped:")
        if not simple_path.exists():
            print(f"  Missing: {simple_path}")
        if not weighted_path.exists():
            print(f"  Missing: {weighted_path}")
        print("\nTo generate these files:")
        print("  1. Edit config/settings.yaml: scom_method = 'simple'")
        print("  2. Run: python .\\src\\boundary_analyzer\\pipeline\\step_06_compute_scom.py")
        print("  3. Run: python .\\src\\boundary_analyzer\\pipeline\\step_07_rank_and_flag.py")
        print("  4. Rename output to service_rank_simple.csv")
        print("  5. Edit config/settings.yaml: scom_method = 'weighted'")
        print("  6. Run: python .\\src\\boundary_analyzer\\pipeline\\step_06_compute_scom.py")
        print("  7. Run: python .\\src\\boundary_analyzer\\pipeline\\step_07_rank_and_flag.py")
        print("  8. Rename output to service_rank_weighted.csv")

    # Compare threshold methods
    # Use the current service_rank.csv as one method
    current_path = base_dir / "service_rank.csv"
    if current_path.exists():
        print("\n\nNote: To compare threshold methods, run step 07 with different threshold_method settings:")
        print("  1. Edit config/settings.yaml: threshold_method = 'fixed'")
        print("  2. Run: python .\\src\\boundary_analyzer\\pipeline\\step_07_rank_and_flag.py")
        print("  3. Rename output to service_rank_fixed.csv")
        print("  4. Edit config/settings.yaml: threshold_method = 'percentile'")
        print("  5. Run: python .\\src\\boundary_analyzer\\pipeline\\step_07_rank_and_flag.py")
        print("  6. Rename output to service_rank_percentile.csv")
        print("  7. Run this comparison script again")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
