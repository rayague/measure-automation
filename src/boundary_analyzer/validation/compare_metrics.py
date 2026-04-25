from __future__ import annotations

from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr


def load_ranking(file_path: Path) -> pd.DataFrame:
    """Load service ranking CSV."""
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    return pd.read_csv(file_path)


def compare_scom_methods(
    simple_path: Path,
    weighted_path: Path,
) -> dict[str, any]:
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
) -> dict[str, any]:
    """Compare fixed, percentile, and z-score threshold methods.
    
    Returns dict with comparison metrics.
    """
    fixed_df = load_ranking(fixed_path)
    percentile_df = load_ranking(percentile_path)
    
    # Extract suspicious services
    fixed_suspicious = set(fixed_df[fixed_df["is_suspicious"] == True]["service_name"])
    percentile_suspicious = set(percentile_df[percentile_df["is_suspicious"] == True]["service_name"])
    
    # Get threshold values
    fixed_threshold = fixed_df["threshold_value"].iloc[0] if "threshold_value" in fixed_df.columns else 0.5
    percentile_threshold = percentile_df["threshold_value"].iloc[0] if "threshold_value" in percentile_df.columns else None
    
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
        zscore_suspicious = set(zscore_df[zscore_df["is_suspicious"] == True]["service_name"])
        zscore_threshold = zscore_df["threshold_value"].iloc[0] if "threshold_value" in zscore_df.columns else None
        
        overlap_zscore = percentile_suspicious & zscore_suspicious
        only_zscore = zscore_suspicious - percentile_suspicious
        
        result.update({
            "zscore_threshold": zscore_threshold,
            "zscore_suspicious_count": len(zscore_suspicious),
            "percentile_zscore_overlap_count": len(overlap_zscore),
            "only_zscore_count": len(only_zscore),
            "percentile_zscore_overlap_services": list(overlap_zscore),
            "only_zscore_services": list(only_zscore),
        })
    
    return result


def print_scom_comparison(comparison: dict[str, any]) -> None:
    """Print SCOM comparison results."""
    print("\n" + "=" * 60)
    print("SCOM METHOD COMPARISON: Simple vs Weighted")
    print("=" * 60)
    
    print(f"\nRank Correlation (Spearman): {comparison['rank_correlation']:.4f}")
    print(f"P-value: {comparison['rank_correlation_p_value']:.4f}")
    
    print("\nRank Change Statistics:")
    stats = comparison["rank_change_stats"]
    print(f"  Mean: {stats['mean']:.2f}")
    print(f"  Std: {stats['std']:.2f}")
    print(f"  Min: {stats['min']:.2f}")
    print(f"  Max: {stats['max']:.2f}")
    
    print("\nSCOM Score Change Statistics:")
    scom_stats = comparison["scom_change_stats"]
    print(f"  Mean: {scom_stats['mean']:.4f}")
    print(f"  Std: {scom_stats['std']:.4f}")
    print(f"  Min: {scom_stats['min']:.4f}")
    print(f"  Max: {scom_stats['max']:.4f}")
    
    print("\nServices with Significant Rank Changes (|change| > 2):")
    if comparison["significant_rank_changes"]:
        for change in comparison["significant_rank_changes"]:
            print(f"  {change['service_name']}: {change['rank_simple']} → {change['rank_weighted']} (change: {change['rank_change']:+.0f})")
    else:
        print("  None")
    
    print("\nTop 5 Lowest SCOM (Simple):", comparison["top_5_simple"])
    print("Top 5 Lowest SCOM (Weighted):", comparison["top_5_weighted"])
    
    print("\nBottom 5 Highest SCOM (Simple):", comparison["bottom_5_simple"])
    print("Bottom 5 Highest SCOM (Weighted):", comparison["bottom_5_weighted"])


def print_threshold_comparison(comparison: dict[str, any]) -> None:
    """Print threshold comparison results."""
    print("\n" + "=" * 60)
    print("THRESHOLD METHOD COMPARISON")
    print("=" * 60)
    
    print(f"\nFixed Threshold: {comparison['fixed_threshold']:.4f}")
    print(f"Percentile Threshold: {comparison['percentile_threshold']:.4f}")
    
    print(f"\nFixed Suspicious Count: {comparison['fixed_suspicious_count']}")
    print(f"Percentile Suspicious Count: {comparison['percentile_suspicious_count']}")
    
    print(f"\nOverlap Count: {comparison['overlap_count']}")
    print(f"Only Fixed Count: {comparison['only_fixed_count']}")
    print(f"Only Percentile Count: {comparison['only_percentile_count']}")
    
    if comparison["overlap_services"]:
        print("\nServices flagged by both methods:", comparison["overlap_services"])
    if comparison["only_fixed_services"]:
        print("\nServices flagged only by fixed:", comparison["only_fixed_services"])
    if comparison["only_percentile_services"]:
        print("\nServices flagged only by percentile:", comparison["only_percentile_services"])
    
    if "zscore_threshold" in comparison:
        print(f"\nZ-Score Threshold: {comparison['zscore_threshold']:.4f}")
        print(f"Z-Score Suspicious Count: {comparison['zscore_suspicious_count']}")
        print(f"Percentile-Z-Score Overlap Count: {comparison['percentile_zscore_overlap_count']}")
        print(f"Only Z-Score Count: {comparison['only_zscore_count']}")


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
