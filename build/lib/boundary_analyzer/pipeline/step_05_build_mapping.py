from __future__ import annotations

import logging

import pandas as pd

from boundary_analyzer.detection.mapping_builder import build_endpoint_table_mapping, save_endpoint_table_map_csv
from boundary_analyzer.settings_loader import get_data_dir

logger = logging.getLogger(__name__)


def main() -> int:
    base_dir = get_data_dir()
    spans_path = base_dir / "interim" / "spans.csv"
    endpoints_path = base_dir / "interim" / "endpoints.csv"
    db_ops_path = base_dir / "interim" / "db_operations.csv"
    output_path = base_dir / "interim" / "endpoint_table_map.csv"

    # Check inputs exist
    for path in [spans_path, endpoints_path, db_ops_path]:
        if not path.exists():
            logger.error("Error: %s not found. Run previous steps first.", path.name)
            return 1

    logger.info("Reading input files...")
    spans_df = pd.read_csv(spans_path)
    endpoints_df = pd.read_csv(endpoints_path)
    db_ops_df = pd.read_csv(db_ops_path)

    logger.info("Loaded %d spans, %d endpoints, %d DB operations", len(spans_df), len(endpoints_df), len(db_ops_df))

    # Build mapping
    mapping_df = build_endpoint_table_mapping(spans_df, endpoints_df, db_ops_df)

    logger.info("Built %d endpoint-table mappings", len(mapping_df))

    if not mapping_df.empty:
        logger.info("\nMapping preview:")
        logger.info("%s", mapping_df.head(10).to_string(index=False))

        # Show summary by service
        logger.info("\nBy service:")
        for service in mapping_df["service_name"].unique():
            service_df = mapping_df[mapping_df["service_name"] == service]
            endpoints = service_df["endpoint_key"].nunique()
            tables = service_df["table"].nunique()
            logger.info("  %s: %d endpoints, %d tables", service, endpoints, tables)

    save_endpoint_table_map_csv(mapping_df, output_path)
    logger.info("\nSaved to: %s", output_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
