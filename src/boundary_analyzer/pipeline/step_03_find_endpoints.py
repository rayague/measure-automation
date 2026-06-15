from __future__ import annotations

import logging

import pandas as pd

from boundary_analyzer.detection.endpoint_extractor import extract_endpoints, save_endpoints_csv
from boundary_analyzer.settings_loader import get_data_dir

logger = logging.getLogger(__name__)


def main() -> int:
    base_dir = get_data_dir()
    spans_path = base_dir / "interim" / "spans.csv"
    output_path = base_dir / "interim" / "endpoints.csv"

    logger.info("Reading spans from: %s", spans_path)

    if not spans_path.exists():
        logger.error("Error: spans.csv not found. Run step 02 first.")
        return 1

    spans_df = pd.read_csv(spans_path)
    logger.info("Loaded %d spans", len(spans_df))

    endpoints_df = extract_endpoints(spans_df)
    logger.info("Found %d endpoint spans", len(endpoints_df))

    if not endpoints_df.empty:
        logger.info("Endpoints: %s", endpoints_df["endpoint_key"].unique().tolist())

    save_endpoints_csv(endpoints_df, output_path)
    logger.info("Saved to: %s", output_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
