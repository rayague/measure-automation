from __future__ import annotations

import logging

import pandas as pd

from boundary_analyzer._utils import save_csv
from boundary_analyzer.metrics.threshold_ultimate import apply_threshold
from boundary_analyzer.settings_loader import get_data_dir, load_settings

logger = logging.getLogger(__name__)


def main() -> int:
    base_dir = get_data_dir()
    scom_path = base_dir / "processed" / "service_scom.csv"
    rank_output_path = base_dir / "processed" / "service_rank.csv"
    suspicious_output_path = base_dir / "processed" / "suspicious_services.csv"

    logger.info("Reading SCOM scores from: %s", scom_path)

    if not scom_path.exists():
        logger.error("Error: service_scom.csv not found. Run step 06 first.")
        return 1

    # Check if file is empty (no data or just headers)
    if scom_path.stat().st_size == 0:
        logger.error("Error: service_scom.csv is empty (0 bytes). No SCOM data to process.")
        logger.error("This usually means no DB operations were found in traces.")
        logger.error("Tip: Check if database traces are appearing in Jaeger.")
        return 3

    # Load settings for threshold method
    settings = load_settings()
    threshold_method = settings.threshold_method
    threshold_percentile = settings.threshold_percentile
    threshold_zscore = settings.threshold_zscore
    fixed_threshold = settings.scom_threshold

    # Try to read CSV, handle empty/malformed case
    try:
        scom_df = pd.read_csv(scom_path)
    except pd.errors.EmptyDataError:
        logger.error("Error: service_scom.csv has no columns (EmptyDataError).")
        logger.error("This happens when no services could be scored (no DB operations found).")
        logger.error("Tip: Check if database traces are appearing in Jaeger.")
        return 3

    logger.info("Loaded %d services", len(scom_df))

    if scom_df.empty:
        logger.error("Error: No services found in service_scom.csv.")
        logger.error("This usually means no traces/spans were collected (Jaeger returned 0 traces).")
        logger.error("Tip: send some traffic to your service, increase lookback_minutes/limit_traces, then re-run.")
        return 2

    # Add rank (sorted by SCOM, lowest first)
    scom_df = scom_df.sort_values("scom_score").reset_index(drop=True)
    scom_df["rank"] = scom_df.index + 1

    # Apply statistical threshold
    logger.info("\nThreshold method: %s", threshold_method)
    if threshold_method == "percentile":
        logger.info("  Percentile: %s%%", threshold_percentile)
    elif threshold_method == "zscore":
        logger.info("  Z-score cutoff: %s", threshold_zscore)
    elif threshold_method == "fixed":
        logger.info("  Fixed threshold: %s", fixed_threshold)

    scom_df = apply_threshold(
        scom_df,
        threshold_method=threshold_method,
        threshold_percentile=threshold_percentile,
        threshold_zscore=threshold_zscore,
        fixed_threshold=fixed_threshold,
    )

    threshold_value = scom_df["threshold_value"].iloc[0]
    suspicious_count = len(scom_df[scom_df["is_suspicious"]])
    logger.info("\nComputed threshold: %.4f", threshold_value)
    logger.info("Suspicious services: %d", suspicious_count)

    # Save ranking
    save_csv(scom_df, rank_output_path)
    logger.info("Saved ranking to: %s", rank_output_path)

    # Save suspicious services only
    suspicious_df = scom_df[scom_df["is_suspicious"]].copy()
    save_csv(suspicious_df, suspicious_output_path)
    logger.info("Saved suspicious services to: %s", suspicious_output_path)

    logger.info("\nSuspicious services (SCOM < %.4f): %d", threshold_value, len(suspicious_df))
    if not suspicious_df.empty:
        logger.info("%s", suspicious_df[["rank", "service_name", "scom_score"]].to_string(index=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
