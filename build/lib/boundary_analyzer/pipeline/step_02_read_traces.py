from __future__ import annotations

import logging

from boundary_analyzer.parsing.trace_reader import read_all_traces, save_spans_csv
from boundary_analyzer.settings_loader import get_data_dir, get_traces_dir, load_settings

logger = logging.getLogger(__name__)


def main() -> int:
    settings = load_settings()

    traces_dir = get_traces_dir(settings)
    interim_dir = get_data_dir() / "interim"
    output_file = interim_dir / "spans.csv"

    logger.info("Reading traces from: %s", traces_dir)
    df = read_all_traces(traces_dir)

    logger.info("Found %d spans", len(df))
    logger.info("Services: %s", df["service_name"].unique().tolist())

    save_spans_csv(df, output_file)
    logger.info("Saved to: %s", output_file)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
