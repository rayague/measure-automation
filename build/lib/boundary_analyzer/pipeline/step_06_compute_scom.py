from __future__ import annotations

import logging
import os

import pandas as pd

from boundary_analyzer.metrics.scom import (
    compute_scom,
    save_scom_csv,
)
from boundary_analyzer.settings_loader import get_data_dir, load_settings

logger = logging.getLogger(__name__)


def main() -> int:
    base_dir = get_data_dir()
    mapping_path = base_dir / "interim" / "endpoint_table_map.csv"
    endpoints_path = base_dir / "interim" / "endpoints.csv"
    output_path = base_dir / "processed" / "service_scom.csv"

    logger.info("Reading mapping from: %s", mapping_path)

    if not mapping_path.exists():
        logger.error("Error: endpoint_table_map.csv not found. Run step 05 first.")
        return 1

    # Load settings for SCOM weighting
    settings = load_settings()
    endpoint_weighting = settings.endpoint_weighting

    # Check CLI flag for skip-no-db-services
    skip_no_db = os.environ.get("BOUNDARY_ANALYZER_SKIP_NO_DB_SERVICES", "").strip() == "1"

    mapping_df = pd.read_csv(mapping_path)
    logger.info("Loaded %d endpoint-table mappings", len(mapping_df))

    endpoints_df = None
    if endpoints_path.exists():
        endpoints_df = pd.read_csv(endpoints_path)
        logger.info("Loaded %d endpoint spans (for endpoint coverage)", len(endpoints_df))
    else:
        logger.warning("Warning: endpoints.csv not found; endpoints without DB ops may be missed.")

    logger.info("\nSCOM endpoint weighting: %s", endpoint_weighting)
    if skip_no_db:
        logger.info("skip_no_db_services: enabled")

    # Compute SCOM faithfully according to the paper
    scom_df = compute_scom(
        mapping_df=mapping_df,
        endpoints_df=endpoints_df,
        use_endpoint_weighting=endpoint_weighting,
        skip_no_db_services=skip_no_db,
    )

    logger.info("\nSCOM Scores:")
    if scom_df.empty:
        logger.info("  (No services to score - no DB operations found)")
        logger.info("  Tip: Check if database traces are being captured in Jaeger.")
    else:
        logger.info("%s", scom_df.to_string(index=False))

    save_scom_csv(scom_df, output_path)
    logger.info("\nSaved to: %s", output_path)

    # Warn if empty but allow pipeline to continue (step_07 will handle it)
    if scom_df.empty:
        logger.warning("\nWarning: SCOM DataFrame is empty. Pipeline will attempt to continue.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
